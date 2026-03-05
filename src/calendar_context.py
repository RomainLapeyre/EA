"""Google Calendar context — fetch upcoming events to inform scheduling decisions."""

import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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

    def get_free_slots(
        self,
        *,
        timezone: str = "America/New_York",
        working_hours_start: int = 9,
        working_hours_end: int = 18,
        slot_duration_minutes: int = 30,
        lookahead_days: int = 7,
        slots_to_propose: int = 3,
    ) -> str:
        """Return a formatted list of free time slots within working hours.

        Returns an empty string on error so callers can treat it as optional context.
        """
        try:
            tz = ZoneInfo(timezone)
        except ZoneInfoNotFoundError:
            logger.warning("Unknown timezone %r — falling back to UTC", timezone)
            tz = ZoneInfo("UTC")

        now = datetime.now(tz)
        end = now + timedelta(days=lookahead_days)

        try:
            result = (
                self.service.events()
                .list(
                    calendarId="primary",
                    timeMin=now.astimezone(ZoneInfo("UTC")).isoformat(),
                    timeMax=end.astimezone(ZoneInfo("UTC")).isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                    maxResults=100,
                )
                .execute()
            )
        except HttpError as exc:
            logger.warning("Could not fetch calendar events for free-slot calc: %s", exc)
            return ""

        busy = _events_to_busy_intervals(result.get("items", []), tz, working_hours_start, working_hours_end)

        free_slots = []
        current_date = now.date()
        end_date = end.date()

        while current_date <= end_date and len(free_slots) < slots_to_propose:
            day_slots = _find_free_slots(
                day=current_date,
                busy=busy,
                tz=tz,
                start_hour=working_hours_start,
                end_hour=working_hours_end,
                duration_minutes=slot_duration_minutes,
                now=now,
            )
            for slot_start, slot_end in day_slots:
                free_slots.append((slot_start, slot_end))
                if len(free_slots) >= slots_to_propose:
                    break
            current_date += timedelta(days=1)

        tz_label = timezone
        if not free_slots:
            return f"=== No free slots found in the next {lookahead_days} days ({tz_label}) ==="

        lines = [f"=== Available slots (next {lookahead_days} days, {tz_label}) ==="]
        for slot_start, slot_end in free_slots:
            lines.append(f"  • {slot_start.strftime('%a %b %d  %H:%M')}–{slot_end.strftime('%H:%M')}")
        return "\n".join(lines)


# ------------------------------------------------------------------
# Free-slot helpers (module-level so they are easy to unit-test)
# ------------------------------------------------------------------

def _events_to_busy_intervals(
    events: list,
    tz: ZoneInfo,
    work_start: int,
    work_end: int,
) -> list[tuple[datetime, datetime]]:
    """Convert event dicts to sorted (start, end) tuples in the target timezone.

    All-day events are treated as blocking the entire working day.
    Cancelled events are ignored.
    """
    intervals: list[tuple[datetime, datetime]] = []
    for ev in events:
        if ev.get("status") == "cancelled":
            continue
        start_raw = ev.get("start", {})
        end_raw = ev.get("end", {})
        if "dateTime" in start_raw:
            start_dt = datetime.fromisoformat(start_raw["dateTime"]).astimezone(tz)
            end_dt = datetime.fromisoformat(end_raw["dateTime"]).astimezone(tz)
            intervals.append((start_dt, end_dt))
        elif "date" in start_raw:
            # All-day event — block the entire workday
            day = date.fromisoformat(start_raw["date"])
            day_start = datetime(day.year, day.month, day.day, work_start, 0, tzinfo=tz)
            day_end = datetime(day.year, day.month, day.day, work_end, 0, tzinfo=tz)
            intervals.append((day_start, day_end))
    intervals.sort(key=lambda x: x[0])
    return intervals


def _find_free_slots(
    day: date,
    busy: list[tuple[datetime, datetime]],
    tz: ZoneInfo,
    start_hour: int,
    end_hour: int,
    duration_minutes: int,
    now: datetime,
) -> list[tuple[datetime, datetime]]:
    """Return free (start, end) pairs on *day* that fit within working hours."""
    work_start = datetime(day.year, day.month, day.day, start_hour, 0, tzinfo=tz)
    work_end = datetime(day.year, day.month, day.day, end_hour, 0, tzinfo=tz)
    duration = timedelta(minutes=duration_minutes)

    # Don't offer slots in the past
    window_start = max(work_start, now)
    if window_start >= work_end:
        return []

    # Collect busy intervals that overlap today's working window
    day_busy: list[tuple[datetime, datetime]] = []
    for b_start, b_end in busy:
        if b_end <= window_start or b_start >= work_end:
            continue
        clipped_start = max(b_start, window_start)
        clipped_end = min(b_end, work_end)
        if clipped_start < clipped_end:
            day_busy.append((clipped_start, clipped_end))
    day_busy.sort(key=lambda x: x[0])

    # Walk through the day finding gaps that fit the requested duration
    free: list[tuple[datetime, datetime]] = []
    cursor = window_start
    for b_start, b_end in day_busy:
        if cursor + duration <= b_start:
            free.append((cursor, cursor + duration))
        cursor = max(cursor, b_end)
    if cursor + duration <= work_end:
        free.append((cursor, cursor + duration))

    return free
