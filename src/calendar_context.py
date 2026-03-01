"""Google Calendar context — fetch upcoming events to inform scheduling decisions."""

import logging
from datetime import datetime, timedelta, timezone

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
_LOOKAHEAD_DAYS = 7


class CalendarContextClient:
    """Fetch upcoming Google Calendar events so the EA can reason about scheduling.

    Uses the same OAuth client credentials as GmailClient.  The refresh token
    must have been authorised with the ``calendar.readonly`` scope — re-run
    ``scripts/setup_gmail_auth.py`` if you need to add this scope.

    Requires the Google Calendar API to be enabled in your Google Cloud project.
    """

    def __init__(self, client_id: str, client_secret: str, refresh_token: str):
        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=_SCOPES,
        )
        creds.refresh(Request())
        self.service = build("calendar", "v3", credentials=creds)

    def get_upcoming_context(self, days: int = _LOOKAHEAD_DAYS) -> str:
        """Return a formatted text block with events for the next *days* days."""
        now = datetime.now(timezone.utc)
        end = now + timedelta(days=days)

        try:
            result = (
                self.service.events()
                .list(
                    calendarId="primary",
                    timeMin=now.isoformat(),
                    timeMax=end.isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                    maxResults=30,
                )
                .execute()
            )
        except HttpError as exc:
            logger.warning("Could not fetch calendar events: %s", exc)
            return ""

        events = result.get("items", [])
        if not events:
            return f"=== Calendar: no events in the next {days} days ==="

        lines = [f"=== Upcoming calendar ({days}-day window) ==="]
        for ev in events:
            start_raw = ev.get("start", {})
            if "dateTime" in start_raw:
                start_dt = datetime.fromisoformat(start_raw["dateTime"])
                end_raw = ev.get("end", {}).get("dateTime", "")
                end_dt = datetime.fromisoformat(end_raw) if end_raw else None
                time_str = start_dt.strftime("%a %b %d %H:%M")
                if end_dt:
                    time_str += f"–{end_dt.strftime('%H:%M')}"
            else:
                time_str = start_raw.get("date", "")

            summary = ev.get("summary", "(No title)")
            attendees = ev.get("attendees", [])
            line = f"  • {time_str}  {summary}"
            if len(attendees) > 1:
                line += f" ({len(attendees)} attendees)"
            if ev.get("status") == "cancelled":
                line += " [CANCELLED]"
            lines.append(line)

        return "\n".join(lines)
