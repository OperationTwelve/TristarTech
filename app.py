import os
from flask import Flask, redirect, request, render_template_string, session
import requests
import urllib.parse
from datetime import datetime, timezone, timedelta
import threading
import time

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', os.urandom(24))

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
USERS = {}  # {character_id: {'character_name': str, 'portrait_url': str, 'access_token': str}}
LOCATION_HISTORY = []  # [{'character_id': int, 'system_id': int, 'system_name': str, ...}]
UPDATE_FREQUENCY = int(os.environ.get('UPDATE_FREQUENCY', 60))

def get_access_token(code):
    payload = {"grant_type": "authorization_code", "code": code}
    auth = (CLIENT_ID, CLIENT_SECRET)
    try:
        response = requests.post(TOKEN_URL, data=payload, auth=auth)
        response.raise_for_status()
        return response.json().get('access_token')
    except requests.RequestException:
        return None

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
        return character_id, character_name, portrait_url
    except requests.RequestException:
        return None, None, None

def get_system_info(system_id):
    try:
        response = requests.get(SYSTEM_URL.format(system_id=system_id))
        response.raise_for_status()
        data = response.json()
        system_name = data.get('name', 'Unknown')
        is_wormhole = data.get('security_class') == 'W'
        return system_name, is_wormhole
    except requests.RequestException:
        return 'Unknown', False

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
        system_name, is_wormhole = get_system_info(system_id)
        location = {
            'character_id': character_id,
            'system_id': system_id,
            'system_name': system_name,
            'is_wormhole': is_wormhole,
            'station_id': data.get('station_id'),
            'structure_id': data.get('structure_id'),
            'timestamp': datetime.now(timezone.utc)
        }
        return location
    except requests.RequestException:
        return None

def log_location(character_id, location):
    if location:
        LOCATION_HISTORY.append(location)

def get_location_history(character_id):
    history = [
        {
            'system_id': entry['system_id'],
            'system_name': entry['system_name'],
            'is_wormhole': entry['is_wormhole'],
            'station_id': entry['station_id'],
            'structure_id': entry['structure_id'],
            'timestamp': entry['timestamp'],
            'color': 'green' if entry['is_wormhole'] else (
                'yellow' if (datetime.now(timezone.utc) - entry['timestamp']).total_seconds() < 24*3600 else
                'red' if (datetime.now(timezone.utc) - entry['timestamp']).total_seconds() >= 48*3600 else 'blue'
            )
        } for entry in LOCATION_HISTORY if entry['character_id'] == character_id
    ]
    return history

def background_location_update():
    while True:
        for character_id, user_data in USERS.items():
            access_token = user_data.get('access_token')
            location = get_location(character_id, access_token)
            if location:
                log_location(character_id, location)
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
        <title>EVE Location Logger</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet" integrity="sha384-QWTKZyjpPEjISv5WaRU9OFeRpok6YctnYmDr5pNlyT2bRjXh0JMhjY6hW+ALEwIH" crossorigin="anonymous">
        <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin="anonymous" />
        <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js" integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin="anonymous"></script>
        <style>
            #map { height: 400px; }
            .navbar-brand img { width: 32px; height: 32px; margin-right: 10px; }
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
                <a class="navbar-brand" href="/">EVE Location Logger</a>
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
                    <div id="map"></div>
                    <ul>
                    {% for entry in history %}
                    <li>{{ entry.timestamp }}: {{ entry.system_name }} (ID: {{ entry.system_id }})
                        {% if entry.station_id %}Station ID: {{ entry.station_id }}{% elif entry.structure_id %}Structure ID: {{ entry.structure_id }}{% endif %}
                        (Color: {{ entry.color }})
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
            var map = L.map('map').setView([0, 0], 2);
            L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                attribution: 'EVE Map'
            }).addTo(map);
            var locations = [
                {% for entry in history %}
                {
                    lat: {{ loop.index * 10 - 50 }},
                    lng: {{ loop.index * 10 - 50 }},
                    name: "{{ entry.system_name }}",
                    color: "{{ entry.color }}"
                },
                {% endfor %}
            ];
            locations.forEach(function(loc) {
                L.circleMarker([loc.lat, loc.lng], {
                    radius: 8,
                    color: loc.color,
                    fillOpacity: 0.8
                }).addTo(map).bindPopup(loc.name);
            });
            if (locations.length > 0) {
                map.fitBounds(locations.map(l => [l.lat, l.lng]));
            }
        </script>
    </body>
    </html>
    """
    return render_template_string(html, character_name=character_name, portrait_url=portrait_url, location=location, history=history, update_frequency=update_frequency)

@app.route('/login')
def login():
    if not CLIENT_ID or not CLIENT_SECRET or not REDIRECT_URI:
        return "Error: CLIENT_ID, CLIENT_SECRET, or REDIRECT_URI not set", 500
    params = {
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "scope": SCOPES,
        "state": "unique-state"
    }
    auth_url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"
    return redirect(auth_url)

@app.route('/callback')
def callback():
    code = request.args.get('code')
    if not code:
        return "Error: No code received", 400

    access_token = get_access_token(code)
    if not access_token:
        return "Error getting access token", 400

    character_id, character_name, portrait_url = get_character_info(access_token)
    if not character_id:
        return "Error verifying character", 400

    USERS[character_id] = {
        'character_name': character_name,
        'portrait_url': portrait_url,
        'access_token': access_token
    }

    session['character_id'] = character_id
    session['character_name'] = character_name
    session['portrait_url'] = portrait_url

    location = get_location(character_id, access_token)
    if location:
        log_location(character_id, location)
        session['location'] = f"{location['system_name']} (ID: {location['system_id']})" + (
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
    except ValueError:
        pass
    return redirect('/')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False)