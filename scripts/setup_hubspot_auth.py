#!/usr/bin/env python3
"""One-time script to generate a HubSpot OAuth2 refresh token.

Run this locally ONCE to authorise the assistant to access your HubSpot account.
The refresh token it produces should be stored as a GitHub Secret.

Prerequisites
-------------
1. In your HubSpot app (developers.hubspot.com → your app → Auth settings):
   - Add  http://localhost:8888/callback  to Redirect URLs
2. Note your App's Client ID and Client Secret from the Auth settings page.

Usage
-----
    python scripts/setup_hubspot_auth.py
"""

import sys
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Event

_AUTH_URL = "https://app.hubspot.com/oauth/authorize"
_TOKEN_URL = "https://api.hubapi.com/oauth/v1/token"
_REDIRECT_URI = "http://localhost:8888/callback"
_SCOPES = "crm.objects.contacts.read crm.objects.companies.read crm.objects.deals.read"

_auth_code: str = ""
_server_done = Event()


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global _auth_code
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        if "code" in params:
            _auth_code = params["code"][0]
            body = b"<h2>Authorised! You can close this tab and return to the terminal.</h2>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)
        else:
            error = params.get("error", ["unknown"])[0]
            body = f"<h2>Error: {error}</h2>".encode()
            self.send_response(400)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)
        _server_done.set()

    def log_message(self, *_):
        pass  # silence request logs


def _exchange_code(client_id: str, client_secret: str, code: str) -> str:
    """Exchange an authorisation code for a refresh token."""
    data = urllib.parse.urlencode(
        {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": _REDIRECT_URI,
            "code": code,
        }
    ).encode()
    req = urllib.request.Request(
        _TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        import json
        with urllib.request.urlopen(req) as resp:
            payload = json.loads(resp.read())
        return payload["refresh_token"]
    except Exception as exc:
        print(f"\nERROR exchanging code for token: {exc}")
        sys.exit(1)


def main() -> None:
    print("=" * 60)
    print("  HubSpot OAuth2 Setup — AI Email Assistant")
    print("=" * 60)
    print()
    print("Before running this script, make sure you have added")
    print("  http://localhost:8888/callback")
    print("to your HubSpot app's Redirect URLs.")
    print("  (App → Auth settings → Redirect URLs → + Add redirect URL)")
    print()

    client_id = input("Paste your HubSpot Client ID:     ").strip()
    client_secret = input("Paste your HubSpot Client Secret: ").strip()

    if not client_id or not client_secret:
        print("ERROR: Client ID and Client Secret are required.")
        sys.exit(1)

    params = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": _REDIRECT_URI,
            "scope": _SCOPES,
        }
    )
    auth_url = f"{_AUTH_URL}?{params}"

    server = HTTPServer(("localhost", 8888), _CallbackHandler)

    print()
    print("Opening your browser to authorise HubSpot access…")
    webbrowser.open(auth_url)
    print("Waiting for authorisation (approve in the browser)…")

    while not _server_done.is_set():
        server.handle_request()

    if not _auth_code:
        print("ERROR: No authorisation code received.")
        sys.exit(1)

    refresh_token = _exchange_code(client_id, client_secret, _auth_code)

    print()
    print("=" * 60)
    print("  SUCCESS!  Add the following as GitHub Actions Secrets")
    print("  Repository → Settings → Secrets and variables → Actions")
    print("=" * 60)
    print()
    print(f"HUBSPOT_CLIENT_ID={client_id}")
    print(f"HUBSPOT_CLIENT_SECRET={client_secret}")
    print(f"HUBSPOT_REFRESH_TOKEN={refresh_token}")
    print()


if __name__ == "__main__":
    main()
