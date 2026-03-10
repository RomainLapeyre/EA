"""Investor CRM — detect investor emails and sync qualified contacts to Notion.

Pipeline (runs after normal email processing):
  1. For every non-skipped email, ask Claude whether the sender is an investor
     and whether Romain replied positively.
  2. If yes, check Google Calendar for a scheduled event with that investor.
  3. If a positive reply *and* a calendar event both exist, upsert the investor
     into the Notion fundraising CRM database.

Required environment variables:
  NOTION_API_KEY              — Notion integration token
  NOTION_INVESTOR_CRM_ID     — ID of the Notion database to write to
                                (the block anchor from the fundraising page URL)
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

try:
    from notion_client import Client as NotionSDKClient
    _NOTION_AVAILABLE = True
except ImportError:
    _NOTION_AVAILABLE = False


class InvestorCRMClient:
    """Orchestrates investor detection and Notion CRM upsert."""

    def __init__(self, notion_api_key: str, crm_database_id: str):
        if not _NOTION_AVAILABLE:
            raise RuntimeError("notion-client is not installed. Run: pip install notion-client")
        self._notion = NotionSDKClient(auth=notion_api_key)
        self._db_id = crm_database_id
        # Cache the database schema so we only fetch it once per run.
        self._schema: dict | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process_email(
        self,
        email: dict,
        investor_meta: dict,
        calendar_service=None,
    ) -> bool:
        """Evaluate *email* for investor CRM eligibility and upsert if qualified.

        Args:
            email:         Parsed email dict (from GmailClient).
            investor_meta: Dict returned by AIAssistant.classify_investor_interaction().
            calendar_service: Optional Google Calendar service object (from
                              CalendarContextClient.service) for event lookup.

        Returns:
            True if the investor was added/updated in Notion, False otherwise.
        """
        if not investor_meta.get("is_investor"):
            return False
        if not investor_meta.get("positive_reply"):
            logger.debug("Investor email from %s — no positive reply yet, skipping CRM.", email["from_email"])
            return False

        # Check calendar for a scheduled event with this investor.
        meeting_date: str = ""
        meeting_title: str = ""
        if calendar_service:
            event = _find_calendar_event_with(calendar_service, email["from_email"])
            if not event:
                logger.debug(
                    "Investor %s: positive reply found but no calendar event — skipping CRM.",
                    email["from_email"],
                )
                return False
            meeting_date = _event_date(event)
            meeting_title = event.get("summary", "")
            logger.info(
                "Investor %s: calendar event found — '%s' on %s",
                email["from_email"],
                meeting_title,
                meeting_date,
            )
        else:
            # Calendar not enabled — add to CRM based on positive reply alone.
            logger.info(
                "Calendar not available; adding investor %s based on positive reply alone.",
                email["from_email"],
            )

        investor_name = investor_meta.get("investor_name") or email.get("from_name") or email["from_email"]
        firm = investor_meta.get("firm", "")

        self._upsert_investor(
            name=investor_name,
            email=email["from_email"],
            firm=firm,
            last_email_date=email.get("date", ""),
            last_email_subject=email.get("subject", ""),
            meeting_date=meeting_date,
            meeting_title=meeting_title,
        )
        return True

    # ------------------------------------------------------------------
    # Notion helpers
    # ------------------------------------------------------------------

    def _get_schema(self) -> dict:
        """Fetch and cache the database property schema."""
        if self._schema is None:
            db = self._notion.databases.retrieve(database_id=self._db_id)
            self._schema = db.get("properties", {})
        return self._schema

    def _upsert_investor(
        self,
        *,
        name: str,
        email: str,
        firm: str,
        last_email_date: str,
        last_email_subject: str,
        meeting_date: str,
        meeting_title: str,
    ) -> None:
        """Create or update an investor entry in the Notion CRM database."""
        schema = self._get_schema()

        # Check if an entry for this email already exists.
        existing = self._find_existing_page(email)
        if existing:
            logger.info("Updating existing Notion CRM entry for %s (%s)", name, email)
            self._notion.pages.update(
                page_id=existing["id"],
                properties=self._build_properties(
                    schema=schema,
                    name=name,
                    email=email,
                    firm=firm,
                    last_email_date=last_email_date,
                    last_email_subject=last_email_subject,
                    meeting_date=meeting_date,
                    meeting_title=meeting_title,
                ),
            )
        else:
            logger.info("Creating new Notion CRM entry for %s (%s)", name, email)
            self._notion.pages.create(
                parent={"database_id": self._db_id},
                properties=self._build_properties(
                    schema=schema,
                    name=name,
                    email=email,
                    firm=firm,
                    last_email_date=last_email_date,
                    last_email_subject=last_email_subject,
                    meeting_date=meeting_date,
                    meeting_title=meeting_title,
                ),
            )

    def _find_existing_page(self, investor_email: str) -> dict | None:
        """Return an existing Notion page for this investor email, or None."""
        try:
            results = self._notion.databases.query(
                database_id=self._db_id,
                filter={
                    "property": "Email",
                    "email": {"equals": investor_email},
                },
                page_size=1,
            )
            pages = results.get("results", [])
            return pages[0] if pages else None
        except Exception as exc:
            # Email property may not exist or filter may fail — fall back to title search.
            logger.debug("Email-based Notion query failed (%s); will create new entry.", exc)
            return None

    def _build_properties(
        self,
        *,
        schema: dict,
        name: str,
        email: str,
        firm: str,
        last_email_date: str,
        last_email_subject: str,
        meeting_date: str,
        meeting_title: str,
    ) -> dict:
        """Build a Notion properties dict, mapping fields to whatever the schema supports."""
        props: dict = {}
        prop_names = {k.lower(): (k, v) for k, v in schema.items()}

        # Title / Name — required
        title_key = _find_title_key(schema)
        if title_key:
            props[title_key] = {"title": [{"text": {"content": name}}]}

        # Email
        if "email" in prop_names:
            real_key, prop = prop_names["email"]
            if prop["type"] == "email":
                props[real_key] = {"email": email}
            elif prop["type"] == "rich_text":
                props[real_key] = {"rich_text": [{"text": {"content": email}}]}

        # Firm / Fund
        for candidate in ("firm", "fund", "company", "organization"):
            if candidate in prop_names:
                real_key, prop = prop_names[candidate]
                if prop["type"] in ("rich_text", "title"):
                    props[real_key] = {"rich_text": [{"text": {"content": firm}}]}
                break

        # Status — set to "In Progress" / "Active" if available
        for candidate in ("status", "stage", "state"):
            if candidate in prop_names:
                real_key, prop = prop_names[candidate]
                ptype = prop["type"]
                if ptype == "select":
                    options = [o["name"] for o in prop.get("select", {}).get("options", [])]
                    status_val = _pick_status(options, ["In Progress", "Active", "Meeting Scheduled", "Interested"])
                    if status_val:
                        props[real_key] = {"select": {"name": status_val}}
                elif ptype == "status":
                    options = [o["name"] for o in prop.get("status", {}).get("options", [])]
                    status_val = _pick_status(options, ["In Progress", "Active", "Meeting Scheduled", "Interested"])
                    if status_val:
                        props[real_key] = {"status": {"name": status_val}}
                break

        # Last interaction / contact date
        for candidate in ("last contact", "last interaction", "last contacted", "last email", "date"):
            if candidate in prop_names:
                real_key, prop = prop_names[candidate]
                if prop["type"] == "date" and last_email_date:
                    iso = _parse_email_date_to_iso(last_email_date)
                    if iso:
                        props[real_key] = {"date": {"start": iso}}
                break

        # Meeting date
        for candidate in ("meeting date", "meeting", "next meeting", "call date"):
            if candidate in prop_names:
                real_key, prop = prop_names[candidate]
                if prop["type"] == "date" and meeting_date:
                    props[real_key] = {"date": {"start": meeting_date}}
                break

        # Notes — last email subject + meeting title as a rich_text note
        note_parts = []
        if last_email_subject:
            note_parts.append(f"Last email: {last_email_subject}")
        if meeting_title:
            note_parts.append(f"Meeting: {meeting_title}")
        note = " | ".join(note_parts)

        for candidate in ("notes", "note", "comments", "description"):
            if candidate in prop_names:
                real_key, prop = prop_names[candidate]
                if prop["type"] == "rich_text" and note:
                    props[real_key] = {"rich_text": [{"text": {"content": note[:2000]}}]}
                break

        return props


# ------------------------------------------------------------------
# Calendar helpers
# ------------------------------------------------------------------

def _find_calendar_event_with(calendar_service, investor_email: str, lookahead_days: int = 60) -> dict | None:
    """Search the primary calendar for an event that includes *investor_email* as an attendee."""
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=lookahead_days)
    # Also look back a bit in case the meeting was recent.
    start = now - timedelta(days=14)

    try:
        result = (
            calendar_service.events()
            .list(
                calendarId="primary",
                timeMin=start.isoformat(),
                timeMax=end.isoformat(),
                singleEvents=True,
                orderBy="startTime",
                maxResults=100,
            )
            .execute()
        )
    except Exception as exc:
        logger.warning("Could not query calendar for investor event: %s", exc)
        return None

    investor_email_lower = investor_email.lower()
    for event in result.get("items", []):
        if event.get("status") == "cancelled":
            continue
        attendees = event.get("attendees", [])
        for att in attendees:
            if att.get("email", "").lower() == investor_email_lower:
                return event
    return None


def _event_date(event: dict) -> str:
    """Return an ISO date string for a calendar event's start."""
    start = event.get("start", {})
    if "dateTime" in start:
        return datetime.fromisoformat(start["dateTime"]).date().isoformat()
    return start.get("date", "")


# ------------------------------------------------------------------
# Notion property helpers
# ------------------------------------------------------------------

def _find_title_key(schema: dict) -> str | None:
    """Return the name of the title property in the schema."""
    for key, prop in schema.items():
        if prop.get("type") == "title":
            return key
    return None


def _pick_status(options: list[str], preferred: list[str]) -> str:
    """Return the first preferred option that exists in the schema, or the first option."""
    options_lower = {o.lower(): o for o in options}
    for p in preferred:
        if p.lower() in options_lower:
            return options_lower[p.lower()]
    return options[0] if options else ""


def _parse_email_date_to_iso(date_str: str) -> str:
    """Best-effort parse of an RFC 2822 email date to ISO 8601 date string."""
    from email.utils import parsedate_to_datetime
    try:
        dt = parsedate_to_datetime(date_str)
        return dt.date().isoformat()
    except Exception:
        # Fallback: extract YYYY-MM-DD pattern if present
        m = re.search(r"\d{4}-\d{2}-\d{2}", date_str)
        return m.group(0) if m else ""
