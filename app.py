from flask import Flask, redirect, request, render_template_string, session
import requests
import urllib.parse
from datetime import datetime, timezone
import os

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY')  # Must be set in Render

# EVE Online OAuth endpoints
AUTH_URL = "https://login.eveonline.com/v2/oauth/authorize"
TOKEN_URL = "https://login.eveonline.com/v2/oauth/token"
VERIFY_URL = "https://esi.evetech.net/verify/"
LOCATION_URL = "https://esi.evetech.net/latest/characters/{character_id}/location/"

# Scopes needed
SCOPES = "esi-location.read_location.v1"

# Get from env vars (set in Render dashboard)
CLIENT_ID = os.environ.get('CLIENT_ID')
CLIENT_SECRET = os.environ.get('CLIENT_SECRET')
REDIRECT_URI = os.environ.get('REDIRECT_URI')

def get_access_token(code):
    payload = {
        "grant_type": "authorization_code",
        "code": code
    }
    auth = (CLIENT_ID, CLIENT_SECRET)
    try:
        response = requests.post(TOKEN_URL, data=payload, auth=auth)
        response.raise_for_status()
        return response.json().get('access_token')
    except requests.RequestException as e:
        return None  # Handle in caller

def get_character_id(access_token):
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        response = requests.get(VERIFY_URL, headers=headers)
        response.raise_for_status()
        return response.json().get('CharacterID')
    except requests.RequestException as e:
        return None

def get_location(character_id, access_token):
    headers = {
        "Authorization": f"Bearer {access_token}",
        "X-Compatibility-Date": "2025-08-26",
        "X-Tenant": "tranquility"
    }
    url = LOCATION_URL.format(character_id=character_id)
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        location = f"Solar System ID: {data['solar_system_id']}"
        if 'station_id' in data:
            location += f", Station ID: {data['station_id']}"
        elif 'structure_id' in data:
            location += f", Structure ID: {data['structure_id']}"
        return location
    except requests.RequestException as e:
        return "Unknown"

def log_location(location):
    with open("eve_location_log.txt", "a") as f:
        f.write(f"{datetime.now(timezone.utc)}: {location}\n")

@app.route('/')
def home():
    html = """
    <!DOCTYPE html>
    <html>
    <head><title>EVE Location Logger</title></head>
    <body>
        <h2>EVE Online Location Logger</h2>
        <p>Log in to track your character's location.</p>
        <a href="/login"><button>Log In</button></a>
        {% if location %}
        <h3>Current Location: {{ location }}</h3>
        {% endif %}
    </body>
    </html>
    """
    return render_template_string(html, location=session.get('location', None))

@app.route('/login')
def login():
    if not CLIENT_ID or not CLIENT_SECRET or not REDIRECT_URI:
        return "Error: Missing CLIENT_ID, CLIENT_SECRET, or REDIRECT_URI environment variables", 500
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

    character_id = get_character_id(access_token)
    if not character_id:
        return "Error verifying character", 400

    location = get_location(character_id, access_token)
    log_location(location)
    session['location'] = location
    return redirect('/')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)