from flask import Flask, redirect, request, render_template_string, session
import requests
import urllib.parse
from datetime import datetime, timezone
import os

app = Flask(__name__)
app.secret_key = os.urandom(24)  # Secure session key

# EVE Online OAuth endpoints
AUTH_URL = "https://login.eveonline.com/v2/oauth/authorize"
TOKEN_URL = "https://login.eveonline.com/v2/oauth/token"
VERIFY_URL = "https://esi.evetech.net/verify/"
LOCATION_URL = "https://esi.evetech.net/latest/characters/{character_id}/location/"

# Your EVE developer app credentials
CLIENT_ID = "4468a9c3580345e78433cb1622fd1616"
CLIENT_SECRET = "eat_1xjey6dISq4WHUxYHkbJvbx0o5Mbw27yS_2L8nYf"
REDIRECT_URI = "https://your-app-url/callback"  # Replace with your app's callback URL
SCOPES = "esi-location.read_location.v1"

def log_location(location):
    with open("eve_location_log.txt", "a") as f:
        f.write(f"{datetime.now(timezone.utc)}: {location}\n")

@app.route('/')
def home():
    # Simple HTML page with Log In button
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

    # Exchange code for access token
    payload = {
        "grant_type": "authorization_code",
        "code": code
    }
    auth = (CLIENT_ID, CLIENT_SECRET)
    try:
        response = requests.post(TOKEN_URL, data=payload, auth=auth)
        response.raise_for_status()
        access_token = response.json().get('access_token')
    except requests.RequestException as e:
        return f"Error getting access token: {e}", 400

    # Get character ID
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        response = requests.get(VERIFY_URL, headers=headers)
        response.raise_for_status()
        character_id = response.json().get('CharacterID')
    except requests.RequestException as e:
        return f"Error verifying character: {e}", 400

    # Get location
    headers.update({"X-Compatibility-Date": "2025-08-26", "X-Tenant": "tranquility"})
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
        log_location(location)
        session['location'] = location
        return redirect('/')
    except requests.RequestException as e:
        return f"Error getting location: {e}", 400

if __name__ == '__main__':
    app.run(debug=True, port=5000)