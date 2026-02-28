#!/usr/bin/env python3
"""One-time script to generate a Gmail OAuth2 refresh token.

Run this locally ONCE to authorise the assistant to access your Gmail account.
The refresh token it produces should be stored as a GitHub Secret.

Prerequisites
-------------
1. Go to https://console.cloud.google.com and create (or select) a project.
2. Enable the Gmail API for that project.
3. Create OAuth 2.0 credentials (type: Desktop application).
4. Download the credentials JSON or note the Client ID and Client Secret.

Usage
-----
    pip install google-auth-oauthlib
    python scripts/setup_gmail_auth.py
"""

import json
import sys

try:
    from google_auth_oauthlib.flow import InstalledAppFlow
except ImportError:
    print("ERROR: google-auth-oauthlib is not installed.")
    print("Run:  pip install google-auth-oauthlib")
    sys.exit(1)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.settings.basic",  # needed to read your signature
]


def main() -> None:
    print("=" * 60)
    print("  Gmail OAuth2 Setup — AI Email Assistant")
    print("=" * 60)
    print()

    client_id = input("Paste your OAuth2 Client ID:     ").strip()
    client_secret = input("Paste your OAuth2 Client Secret: ").strip()

    if not client_id or not client_secret:
        print("ERROR: Client ID and Client Secret are required.")
        sys.exit(1)

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }

    print()
    print("A browser window will open. Sign in and grant access.")
    print()

    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    creds = flow.run_local_server(port=0)

    print()
    print("=" * 60)
    print("  SUCCESS!  Add the following as GitHub Actions Secrets")
    print("  Repository → Settings → Secrets and variables → Actions")
    print("=" * 60)
    print()
    print(f"GMAIL_CLIENT_ID={client_id}")
    print(f"GMAIL_CLIENT_SECRET={client_secret}")
    print(f"GMAIL_REFRESH_TOKEN={creds.refresh_token}")
    print()
    print("Also add:  ANTHROPIC_API_KEY=<your-key>")
    print()


if __name__ == "__main__":
    main()
