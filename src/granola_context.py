"""Granola meeting notes context — fetch recent meetings for a given sender."""

import logging
import re
from datetime import datetime, timedelta, timezone

import requests

logger = logging.getLogger(__name__)

_AUTH_URL = "https://auth.granola.ai/user_management/authenticate"
_API_BASE = "https://api.granola.ai/v1"
_CLIENT_ID = "client_01JZJ0XBDAT8PHJWQY09Y0VD61"
_TIMEOUT = 10
_LOOKBACK_DAYS = 90
_FETCH_LIMIT = 200


class GranolaContextClient:
    """Fetch Granola meeting context for a given email sender.

    Requires ``GRANOLA_REFRESH_TOKEN`` (WorkOS refresh token from the Granola
    desktop app's ``~/Library/Application Support/Granola/supabase.json``).
    """

    def __init__(self, refresh_token: str):
        self._refresh_token = refresh_token
        self._access_token: str | None = None
        self._session = requests.Session()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_meeting_context(self, sender_email: str, sender_name: str = "") -> str:
        """Return formatted text with recent Granola meetings involving *sender_email*."""
        try:
            self._ensure_token()
        except Exception as exc:
            logger.warning("Granola auth failed: %s", exc)
            return ""

        try:
            docs = self._fetch_recent_documents()
        except Exception as exc:
            logger.warning("Granola document fetch failed: %s", exc)
            return ""

        matches = _find_relevant_meetings(docs, sender_email, sender_name)
        if not matches:
            return ""

        lines = ["=== Granola meeting notes ==="]
        for doc in matches:
            date_str = _fmt_date(doc.get("created_at", ""))
            title = doc.get("title", "Untitled meeting")
            attendees = _attendee_emails(doc)
            line = f"• {date_str} — {title}"
            if attendees:
                others = [e for e in attendees if "romain" not in e.lower()]
                if others:
                    line += f" (with {', '.join(others[:3])})"
            nm = (doc.get("notes_markdown") or "").strip()
            np_ = (doc.get("notes_plain") or "").strip()
            notes = nm or np_
            if notes:
                line += f"\n  Notes: {notes[:300]}"
            lines.append(line)

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_token(self) -> None:
        """Refresh the WorkOS access token if needed."""
        resp = self._session.post(
            _AUTH_URL,
            json={
                "grant_type": "refresh_token",
                "refresh_token": self._refresh_token,
                "client_id": _CLIENT_ID,
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        self._access_token = data["access_token"]
        # Store refreshed refresh_token if rotated
        if "refresh_token" in data:
            self._refresh_token = data["refresh_token"]
        self._session.headers.update({"Authorization": f"Bearer {self._access_token}"})
        logger.debug("Granola access token refreshed")

    def _fetch_recent_documents(self) -> list[dict]:
        """Fetch recent meeting documents from Granola API."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=_LOOKBACK_DAYS)
        docs: list[dict] = []
        offset = 0
        batch = 50

        while len(docs) < _FETCH_LIMIT:
            resp = self._session.post(
                f"{_API_BASE}/get-documents",
                json={"limit": batch, "order": "desc", "offset": offset},
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            page: list[dict] = resp.json()
            if not page:
                break

            for doc in page:
                created = doc.get("created_at", "")
                if created:
                    try:
                        dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                        if dt < cutoff:
                            return docs  # sorted desc, stop early
                    except ValueError:
                        pass
                docs.append(doc)

            if len(page) < batch:
                break
            offset += batch

        return docs


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------

def _find_relevant_meetings(
    docs: list[dict], sender_email: str, sender_name: str
) -> list[dict]:
    """Return docs where the sender was a participant."""
    sender_lower = sender_email.lower()
    # Extract first/last name parts for title matching
    name_parts = [p.lower() for p in re.split(r"[\s.@+]", sender_name) if len(p) > 2]
    # Also try name from email local part
    local = sender_email.split("@")[0].lower()
    email_name_parts = [p for p in re.split(r"[._+\-]", local) if len(p) > 2]
    search_terms = list(set(name_parts + email_name_parts))

    matches = []
    for doc in docs:
        if doc.get("deleted_at"):
            continue
        # Match by attendee email
        if any(a.lower() == sender_lower for a in _attendee_emails(doc)):
            matches.append(doc)
            continue
        # Match by name in title
        title_lower = (doc.get("title") or "").lower()
        if search_terms and any(term in title_lower for term in search_terms):
            matches.append(doc)

    return matches[:5]  # cap at 5 most recent


def _attendee_emails(doc: dict) -> list[str]:
    people = doc.get("people") or {}
    attendees = people.get("attendees") or []
    emails = []
    for a in attendees:
        if isinstance(a, dict):
            e = a.get("email", "")
            if e:
                emails.append(e)
    # Also check google_calendar_event attendees
    gcal = doc.get("google_calendar_event") or {}
    for a in gcal.get("attendees") or []:
        if isinstance(a, dict):
            e = a.get("email", "")
            if e and e not in emails:
                emails.append(e)
    return emails


def _fmt_date(iso: str) -> str:
    if not iso:
        return "unknown date"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return iso[:10]
