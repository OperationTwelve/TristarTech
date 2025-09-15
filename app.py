import os
from flask import Flask, redirect, request, render_template_string, session
import requests
import urllib.parse
from datetime import datetime, timezone, timedelta
import threading
import time
import base64
import logging

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', os.urandom(24))

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# EVE Online OAuth and API endpoints
AUTH_URL = "https://login.eveonline.com/v2/oauth/authorize"
TOKEN_URL = "https://login.eveonline.com/v2/oauth/token"
VERIFY_URL = "https://esi.evetech.net/verify/"
LOCATION_URL = "https://esi.evetech.net/latest/characters/{character_id}/location/"
SYSTEM_URL = "https://esi.evetech.net/latest/universe/systems/{system_id}/"
CHARACTER_URL = "https://esi.evetech.net/latest/characters/{character_id}/"
PORTRAIT_URL = "https://esi.evetech.net/latest/characters/{character_id}/portrait/"

SCOPES = "esi-location.read_location.v1"
CLIENT_ID = os.environ.get('CLIENT_ID')
CLIENT_SECRET = os.environ.get('CLIENT_SECRET')
REDIRECT_URI = os.environ.get('REDIRECT_URI')

# In-memory storage
USERS = {}  # {character_id: {'character_name': str, 'portrait_url': str, 'access_token': str, 'refresh_token': str}}
LOCATION_HISTORY = []  # [{'character_id': int, 'system_id': int, 'system_name': str, 'security_status': float, ...}]
UPDATE_FREQUENCY = int(os.environ.get('UPDATE_FREQUENCY', 10))

def get_access_token(code):
    payload = {"grant_type": "authorization_code", "code": code}
    auth_string = f"{CLIENT_ID}:{CLIENT_SECRET}"
    headers = {
        "Authorization": f"Basic {base64.b64encode(auth_string.encode()).decode()}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    try:
        response = requests.post(TOKEN_URL, data=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
        logger.info(f"Access token retrieved for code: {code[:10]}...")
        return data.get('access_token'), data.get('refresh_token')
    except requests.RequestException as e:
        logger.error(f"Error getting access token: {e}")
        return None, None

def refresh_access_token(refresh_token):
    payload = {"grant_type": "refresh_token", "refresh_token": refresh_token}
    auth_string = f"{CLIENT_ID}:{CLIENT_SECRET}"
    headers = {
        "Authorization": f"Basic {base64.b64encode(auth_string.encode()).decode()}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    try:
        response = requests.post(TOKEN_URL, data=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
        logger.info("Access token refreshed successfully")
        return data.get('access_token'), data.get('refresh_token')
    except requests.RequestException as e:
        logger.error(f"Error refreshing access token: {e}")
        return None, None

def get_character_info(access_token):
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        response = requests.get(VERIFY_URL, headers=headers)
        response.raise_for_status()
        char_data = response.json()
        character_id = char_data.get('CharacterID')
        character_name = char_data.get('CharacterName')
        portrait_response = requests.get(PORTRAIT_URL.format(character_id=character_id))
        portrait_response.raise_for_status()
        portrait_url = portrait_response.json().get('px128x128', '')
        logger.info(f"Character info retrieved for ID: {character_id}")
        return character_id, character_name, portrait_url
    except requests.RequestException as e:
        logger.error(f"Error getting character info: {e}")
        return None, None, None

def get_system_info(system_id):
    try:
        response = requests.get(SYSTEM_URL.format(system_id=system_id))
        response.raise_for_status()
        data = response.json()
        system_name = data.get('name', 'Unknown')
        security_status = round(data.get('security_status', 0.0), 1)
        is_wormhole = data.get('security_class') == 'W'
        logger.info(f"System info for ID {system_id}: {system_name}, Sec: {security_status}")
        return system_name, security_status, is_wormhole
    except requests.RequestException as e:
        logger.error(f"Error getting system info for ID {system_id}: {e}")
        return 'Unknown', 0.0, False

def get_location(character_id, access_token):
    headers = {
        "Authorization": f"Bearer {access_token}",
        "X-Compatibility-Date": "2025-08-26",
        "X-Tenant": "tranquility"
    }
    try:
        response = requests.get(LOCATION_URL.format(character_id=character_id), headers=headers)
        response.raise_for_status()
        data = response.json()
        system_id = data['solar_system_id']
        system_name, security_status, is_wormhole = get_system_info(system_id)
        location = {
            'character_id': character_id,
            'system_id': system_id,
            'system_name': system_name,
            'security_status': -1.0 if is_wormhole else security_status,
            'is_wormhole': is_wormhole,
            'station_id': data.get('station_id'),
            'structure_id': data.get('structure_id'),
            'timestamp': datetime.now(timezone.utc)
        }
        logger.info(f"Location retrieved for character {character_id}: {system_name}")
        return location
    except requests.RequestException as e:
        logger.error(f"Error getting location for character {character_id}: {e}")
        return None

def log_location(character_id, location):
    if location:
        global LOCATION_HISTORY
        LOCATION_HISTORY = [entry for entry in LOCATION_HISTORY if not (entry['character_id'] == character_id and entry['system_id'] == location['system_id'])]
        LOCATION_HISTORY.append(location)
        logger.info(f"Logged location for character {character_id}: {location['system_name']}")

def get_location_history(character_id):
    history = [
        {
            'system_id': entry['system_id'],
            'system_name': entry['system_name'],
            'security_status': entry['security_status'],
            'is_wormhole': entry['is_wormhole'],
            'station_id': entry['station_id'],
            'structure_id': entry['structure_id'],
            'timestamp': entry['timestamp'],
            'color': 'green' if entry['is_wormhole'] and (datetime.now(timezone.utc) - entry['timestamp']).total_seconds() < 24*3600 else
                    'yellow' if entry['is_wormhole'] and (datetime.now(timezone.utc) - entry['timestamp']).total_seconds() < 48*3600 else
                    'red' if entry['is_wormhole'] and (datetime.now(timezone.utc) - entry['timestamp']).total_seconds() >= 48*3600 else
                    'blue'  # Non-wormhole default
        } for entry in LOCATION_HISTORY if entry['character_id'] == character_id
    ]
    return sorted(history, key=lambda x: x['timestamp'], reverse=True)

def background_location_update():
    while True:
        for character_id, user_data in list(USERS.items()):
            access_token = user_data.get('access_token')
            refresh_token = user_data.get('refresh_token')
            location = get_location(character_id, access_token)
            if not location and refresh_token:
                logger.info(f"Attempting to refresh token for character {character_id}")
                new_access_token, new_refresh_token = refresh_access_token(refresh_token)
                if new_access_token:
                    USERS[character_id]['access_token'] = new_access_token
                    USERS[character_id]['refresh_token'] = new_refresh_token
                    logger.info(f"Token refreshed for character {character_id}")
                    location = get_location(character_id, new_access_token)
                else:
                    logger.error(f"Failed to refresh token for character {character_id}")
            if location:
                log_location(character_id, location)
                with app.app_context():
                    session['location'] = f"{location['system_name']} (ID: {location['system_id']}, Sec: {location['security_status']})" + (
                        f", Station ID: {location['station_id']}" if location['station_id'] else
                        f", Structure ID: {location['structure_id']}" if location['structure_id'] else ""
                    )
        time.sleep(UPDATE_FREQUENCY)

# Start background thread
threading.Thread(target=background_location_update, daemon=True).start()

@app.route('/')
def home():
    character_id = session.get('character_id')
    character_name = USERS.get(character_id, {}).get('character_name') if character_id else None
    portrait_url = USERS.get(character_id, {}).get('portrait_url') if character_id else None
    location = session.get('location', None)
    history = get_location_history(character_id) if character_id else []
    update_frequency = UPDATE_FREQUENCY

    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>TriStar Tools for EVE Online</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet" integrity="sha384-QWTKZyjpPEjISv5WaRU9OFeRpok6YctnYmDr5pNlyT2bRjXh0JMhjY6hW+ALEwIH" crossorigin="anonymous">
        <script src="https://d3js.org/d3.v7.min.js"></script>
        <style>
            .navbar-brand img { width: 32px; height: 32px; margin-right: 10px; }
            #graph { width: 100%; height: 400px; }
            .node text { font-size: 12px; }
            .node.current text { font-weight: bold; }
            .link { stroke: #999; stroke-opacity: 0.6; }
        </style>
    </head>
    <body>
        <nav class="navbar navbar-expand-lg navbar-light bg-light">
            <div class="container-fluid">
                {% if character_name %}
                <a class="navbar-brand" href="/">
                    <img src="{{ portrait_url }}" alt="Portrait">
                    {{ character_name }}
                </a>
                {% else %}
                <a class="navbar-brand" href="/">TriStar Tools for EVE Online</a>
                {% endif %}
                <div class="collapse navbar-collapse">
                    <ul class="nav nav-tabs me-auto mb-2 mb-lg-0">
                        <li class="nav-item">
                            <a class="nav-link active" href="#overview" data-bs-toggle="tab" role="tab">Overview</a>
                        </li>
                        <li class="nav-item">
                            <a class="nav-link" href="#history" data-bs-toggle="tab" role="tab">Location History</a>
                        </li>
                        <li class="nav-item">
                            <a class="nav-link" href="#settings" data-bs-toggle="tab" role="tab">Settings</a>
                        </li>
                    </ul>
                    {% if not character_name %}
                    <a href="/login" class="btn btn-primary">Log In</a>
                    {% endif %}
                </div>
            </div>
        </nav>
        <div class="container mt-4">
            <div class="tab-content">
                <div class="tab-pane fade show active" id="overview" role="tabpanel">
                    <h3>Overview</h3>
                    {% if location %}
                    <p>Current Location: {{ location }}</p>
                    {% else %}
                    <p>Please log in to view your current location.</p>
                    {% endif %}
                </div>
                <div class="tab-pane fade" id="history" role="tabpanel">
                    <h3>Location History</h3>
                    <svg id="graph"></svg>
                    <ul>
                    {% for entry in history %}
                    <li style="color: {{ entry.color }}">{{ entry.timestamp }}: {{ entry.system_name }} (ID: {{ entry.system_id }}, Sec: {{ entry.security_status }})
                        {% if entry.station_id %}Station ID: {{ entry.station_id }}{% elif entry.structure_id %}Structure ID: {{ entry.structure_id }}{% endif %}
                    </li>
                    {% endfor %}
                    </ul>
                </div>
                <div class="tab-pane fade" id="settings" role="tabpanel">
                    <h3>Settings</h3>
                    <form method="POST" action="/update_settings">
                        <div class="mb-3">
                            <label for="update_frequency" class="form-label">Location Update Frequency (seconds):</label>
                            <input type="number" class="form-control" id="update_frequency" name="update_frequency" value="{{ update_frequency }}" min="10">
                        </div>
                        <button type="submit" class="btn btn-primary">Save</button>
                    </form>
                </div>
            </div>
        </div>
        <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js" integrity="sha384-YvpcrYf0tY3lHB60NNkmXc5s9fDVZLESaAA55NDzOxhy9GkcIdslK1eN7N6jIeHz" crossorigin="anonymous"></script>
        <script>
            console.log('Bootstrap script loaded');
            document.addEventListener('DOMContentLoaded', function() {
                try {
                    var tabs = new bootstrap.Tab(document.querySelector('.nav-tabs .nav-link.active'));
                    console.log('Tabs initialized');
                } catch (e) {
                    console.error('Error initializing tabs:', e);
                }
            });
            // D3.js graph
            const history = [
                {% for entry in history %}
                {
                    system_id: {{ entry.system_id }},
                    system_name: "{{ entry.system_name }}",
                    security_status: {{ entry.security_status }},
                    is_wormhole: {{ entry.is_wormhole | tojson }},
                    timestamp: "{{ entry.timestamp.isoformat() }}",
                    color: "{{ entry.color }}",
                    is_current: {{ loop.first | tojson }}
                },
                {% endfor %}
            ];
            if (history.length > 0) {
                const svg = d3.select("#graph")
                    .attr("width", 600)
                    .attr("height", 400);
                const nodes = history.map((d, i) => ({
                    id: d.system_id,
                    name: `${d.system_name} (${d.security_status})`,
                    color: d.color,
                    is_current: d.is_current
                }));
                const links = [];
                for (let i = 0; i < history.length - 1; i++) {
                    links.push({
                        source: history[i].system_id,
                        target: history[i + 1].system_id
                    });
                }
                const simulation = d3.forceSimulation(nodes)
                    .force("link", d3.forceLink(links).id(d => d.id).distance(100))
                    .force("charge", d3.forceManyBody().strength(-200))
                    .force("center", d3.forceCenter(300, 200));
                const link = svg.append("g")
                    .attr("class", "link")
                    .selectAll("line")
                    .data(links)
                    .enter().append("line")
                    .attr("stroke", "#999")
                    .attr("stroke-opacity", 0.6);
                const node = svg.append("g")
                    .attr("class", "node")
                    .selectAll("g")
                    .data(nodes)
                    .enter().append("g")
                    .attr("class", d => d.is_current ? "node current" : "node");
                node.append("circle")
                    .attr("r", 8)
                    .attr("fill", d => d.color);
                node.append("text")
                    .attr("dx", 12)
                    .attr("dy", ".35em")
                    .text(d => d.name);
                simulation.on("tick", () => {
                    link
                        .attr("x1", d => d.source.x)
                        .attr("y1", d => d.source.y)
                        .attr("x2", d => d.target.x)
                        .attr("y2", d => d.target.y);
                    node
                        .attr("transform", d => `translate(${d.x},${d.y})`);
                });
            }
        </script>
    </body>
    </html>
    """
    return render_template_string(html, character_name=character_name, portrait_url=portrait_url, location=location, history=history, update_frequency=update_frequency)

@app.route('/login')
def login():
    if not CLIENT_ID or not CLIENT_SECRET or not REDIRECT_URI:
        logger.error("Missing OAuth configuration")
        return "Error: CLIENT_ID, CLIENT_SECRET, or REDIRECT_URI not set", 500
    params = {
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "scope": SCOPES,
        "state": "unique-state"
    }
    auth_url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"
    logger.info("Redirecting to EVE OAuth login")
    return redirect(auth_url)

@app.route('/callback')
def callback():
    code = request.args.get('code')
    if not code:
        logger.error("No code received in callback")
        return "Error: No code received", 400

    access_token, refresh_token = get_access_token(code)
    if not access_token:
        logger.error("Failed to get access token")
        return "Error getting access token", 400

    character_id, character_name, portrait_url = get_character_info(access_token)
    if not character_id:
        logger.error("Failed to verify character")
        return "Error verifying character", 400

    USERS[character_id] = {
        'character_name': character_name,
        'portrait_url': portrait_url,
        'access_token': access_token,
        'refresh_token': refresh_token
    }
    logger.info(f"User authenticated: {character_name} (ID: {character_id})")

    session['character_id'] = character_id
    session['character_name'] = character_name
    session['portrait_url'] = portrait_url

    location = get_location(character_id, access_token)
    if location:
        log_location(character_id, location)
        session['location'] = f"{location['system_name']} (ID: {location['system_id']}, Sec: {location['security_status']})" + (
            f", Station ID: {location['station_id']}" if location['station_id'] else
            f", Structure ID: {location['structure_id']}" if location['structure_id'] else ""
        )
    return redirect('/')

@app.route('/update_settings', methods=['POST'])
def update_settings():
    global UPDATE_FREQUENCY
    try:
        UPDATE_FREQUENCY = int(request.form.get('update_frequency', 60))
        if UPDATE_FREQUENCY < 10:
            UPDATE_FREQUENCY = 10
        logger.info(f"Update frequency set to {UPDATE_FREQUENCY} seconds")
    except ValueError:
        logger.error("Invalid update frequency input")
    return redirect('/')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False)