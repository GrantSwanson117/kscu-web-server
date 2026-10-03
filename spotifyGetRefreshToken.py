import os
import secrets
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qs

import httpx
from dotenv import load_dotenv, set_key

ENV_PATH = Path(__file__).parent / ".env"
load_dotenv(ENV_PATH)

CLIENT_ID = os.getenv("CLIENT_ID").strip()
CLIENT_SECRET = os.getenv("CLIENT_SECRET").strip()
REDIRECT_URI = os.getenv("REDIRECT_URI").strip()

parsed = urlparse(REDIRECT_URI)
HOST, PORT, PATH = parsed.hostname, parsed.port or 80, parsed.path or "/"


class CallbackHandler(BaseHTTPRequestHandler):
    result = {}

    def do_GET(self):
        url = urlparse(self.path)
        if url.path != PATH:
            self.send_response(404)
            self.end_headers()
            return
        CallbackHandler.result = {k: v[0] for k, v in parse_qs(url.query).items()}
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<h2>Authorized. You can close this tab.</h2>")

    def log_message(self, *args): 
        pass


def get_my_refresh_token():
    state = secrets.token_urlsafe(16)
    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": "user-read-currently-playing user-read-recently-played",
        "state": state,
    }
    auth_url = f"https://accounts.spotify.com/authorize?{urlencode(params)}"

    server = HTTPServer((HOST, PORT), CallbackHandler)
    print("Opening your browser to authorize...")
    print(f"If it doesn't open, visit:\n{auth_url}\n")
    webbrowser.open(auth_url)

    while not CallbackHandler.result:
        server.handle_request()
    server.server_close()

    result = CallbackHandler.result
    if "error" in result:
        raise SystemExit(f"Spotify returned an error: {result['error']}")
    if result.get("state") != state:
        raise SystemExit("State mismatch; aborting.")

    response = httpx.post(
        "https://accounts.spotify.com/api/token",
        data={
            "grant_type": "authorization_code",
            "code": result["code"],
            "redirect_uri": REDIRECT_URI,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
    )
    data = response.json()

    if "refresh_token" in data:
        set_key(str(ENV_PATH), "SPOTIFY_REFRESH_TOKEN", data["refresh_token"], quote_mode="never")
        print(f"SUCCESS! Saved SPOTIFY_REFRESH_TOKEN to {ENV_PATH}")
    else:
        print("ERROR from Spotify:")
        print(data)


if __name__ == "__main__":
    get_my_refresh_token()