"""Gmail API client — fetch unread emails and create draft replies."""

import base64
import logging
import re
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import html2text
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

PROCESSED_LABEL = "EA/Processed"
NEWSLETTER_LABEL = "EA/Newsletter"
SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.compose",
]

# Headers that indicate automated / bulk email — skip these.
_AUTOMATED_HEADERS = {"list-unsubscribe", "list-id", "x-mailchimp-id", "x-campaign"}


class GmailClient:
    def __init__(self, client_id: str, client_secret: str, refresh_token: str):
        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=SCOPES,
        )
        creds.refresh(Request())
        self.service = build("gmail", "v1", credentials=creds)
        self._processed_label_id = self._get_or_create_label(PROCESSED_LABEL)
        self._newsletter_label_id = self._get_or_create_label(NEWSLETTER_LABEL)

    # ------------------------------------------------------------------
    # Label management
    # ------------------------------------------------------------------

    def _get_or_create_label(self, name: str) -> str:
        """Return the label ID for *name*, creating it if necessary."""
        try:
            labels = self.service.users().labels().list(userId="me").execute()
            for label in labels.get("labels", []):
                if label["name"] == name:
                    return label["id"]
            label = (
                self.service.users()
                .labels()
                .create(
                    userId="me",
                    body={
                        "name": name,
                        "labelListVisibility": "labelHide",
                        "messageListVisibility": "hide",
                    },
                )
                .execute()
            )
            logger.info("Created Gmail label: %s", name)
            return label["id"]
        except HttpError as exc:
            logger.error("Error managing label %s: %s", name, exc)
            raise

    # ------------------------------------------------------------------
    # Fetching emails
    # ------------------------------------------------------------------

    def get_draft_thread_ids(self) -> set[str]:
        """Return the set of thread IDs that already have a draft."""
        try:
            result = self.service.users().drafts().list(userId="me").execute()
            thread_ids: set[str] = set()
            for draft in result.get("drafts", []):
                tid = draft.get("message", {}).get("threadId")
                if tid:
                    thread_ids.add(tid)
            return thread_ids
        except HttpError as exc:
            logger.warning("Could not fetch existing drafts: %s", exc)
            return set()

    def get_unprocessed_emails(self) -> list[dict]:
        """Return all primary inbox emails not yet processed and without existing drafts."""
        query = f"in:inbox category:primary -label:{PROCESSED_LABEL}"
        draft_thread_ids = self.get_draft_thread_ids()
        try:
            emails = []
            page_token = None
            while True:
                kwargs: dict = {"userId": "me", "q": query}
                if page_token:
                    kwargs["pageToken"] = page_token
                result = self.service.users().messages().list(**kwargs).execute()
                for ref in result.get("messages", []):
                    parsed = self._parse_message(ref["id"])
                    if parsed and not self._is_automated(parsed):
                        if parsed["thread_id"] not in draft_thread_ids:
                            emails.append(parsed)
                page_token = result.get("nextPageToken")
                if not page_token:
                    break
            return emails
        except HttpError as exc:
            logger.error("Error listing emails: %s", exc)
            raise

    def _parse_message(self, message_id: str) -> dict | None:
        """Fetch and parse a single Gmail message into a plain dict."""
        try:
            msg = (
                self.service.users()
                .messages()
                .get(userId="me", id=message_id, format="full")
                .execute()
            )
            headers = {
                h["name"].lower(): h["value"]
                for h in msg["payload"].get("headers", [])
            }

            subject = headers.get("subject", "(No Subject)")
            from_header = headers.get("from", "")
            to_header = headers.get("to", "")
            date = headers.get("date", "")
            message_id_header = headers.get("message-id", "")

            body = self._extract_body(msg["payload"])

            return {
                "id": message_id,
                "thread_id": msg["threadId"],
                "message_id_header": message_id_header,
                "subject": subject,
                "from": from_header,
                "from_email": _extract_email(from_header),
                "from_name": _extract_name(from_header),
                "to": to_header,
                "date": date,
                "body": body,
                "snippet": msg.get("snippet", ""),
                "raw_headers": headers,
            }
        except HttpError as exc:
            logger.error("Error parsing message %s: %s", message_id, exc)
            return None

    def get_thread_history(self, thread_id: str, current_message_id: str) -> str:
        """Return earlier messages in a thread as a formatted string."""
        try:
            thread = (
                self.service.users()
                .threads()
                .get(userId="me", id=thread_id, format="full")
                .execute()
            )
            parts = []
            for msg in thread.get("messages", []):
                if msg["id"] == current_message_id:
                    break
                headers = {
                    h["name"].lower(): h["value"]
                    for h in msg["payload"].get("headers", [])
                }
                sender = headers.get("from", "Unknown")
                date = headers.get("date", "")
                body = self._extract_body(msg["payload"])[:800]
                parts.append(f"[{date}] {sender}:\n{body}")
            return "\n\n---\n\n".join(parts) if parts else ""
        except HttpError as exc:
            logger.warning("Could not fetch thread %s: %s", thread_id, exc)
            return ""

    # ------------------------------------------------------------------
    # Signature
    # ------------------------------------------------------------------

    def get_signature(self) -> str:
        """Return the plain-text version of the user's primary Gmail signature.

        Requires the gmail.settings.basic scope.  Returns an empty string if
        the scope is missing or no signature is configured.
        """
        try:
            result = (
                self.service.users()
                .settings()
                .sendAs()
                .list(userId="me")
                .execute()
            )
            for send_as in result.get("sendAs", []):
                if send_as.get("isDefault"):
                    raw_sig = send_as.get("signature", "")
                    if raw_sig:
                        converter = html2text.HTML2Text()
                        converter.ignore_links = False
                        converter.ignore_images = True
                        converter.body_width = 0
                        return converter.handle(raw_sig).strip()
            return ""
        except HttpError as exc:
            logger.warning("Could not fetch Gmail signature: %s", exc)
            return ""

    # ------------------------------------------------------------------
    # Drafts
    # ------------------------------------------------------------------

    def create_draft_reply(
        self, original_email: dict, draft_body: str, signature: str = ""
    ) -> str:
        """Create a Gmail draft as a reply to *original_email*.

        If *signature* is provided it is appended after the draft body,
        separated by the conventional ``-- `` delimiter.
        """
        if signature:
            full_body = f"{draft_body}\n\n-- \n{signature}"
        else:
            full_body = draft_body

        msg = MIMEMultipart()
        msg["To"] = original_email["from"]
        msg["Subject"] = (
            original_email["subject"]
            if original_email["subject"].lower().startswith("re:")
            else f"Re: {original_email['subject']}"
        )
        if original_email["message_id_header"]:
            msg["In-Reply-To"] = original_email["message_id_header"]
            msg["References"] = original_email["message_id_header"]
        msg.attach(MIMEText(full_body, "plain"))

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        draft = (
            self.service.users()
            .drafts()
            .create(
                userId="me",
                body={
                    "message": {
                        "raw": raw,
                        "threadId": original_email["thread_id"],
                    }
                },
            )
            .execute()
        )
        logger.info("Draft %s created for thread %s", draft["id"], original_email["thread_id"])
        return draft["id"]

    # ------------------------------------------------------------------
    # State tracking
    # ------------------------------------------------------------------

    def mark_as_processed(self, message_id: str) -> None:
        """Add the EA/Processed label so the email is not picked up again."""
        self.service.users().messages().modify(
            userId="me",
            id=message_id,
            body={"addLabelIds": [self._processed_label_id]},
        ).execute()

    def archive_as_newsletter(self, message_id: str) -> None:
        """Label as EA/Newsletter, remove from inbox, and mark processed."""
        self.service.users().messages().modify(
            userId="me",
            id=message_id,
            body={
                "addLabelIds": [self._newsletter_label_id, self._processed_label_id],
                "removeLabelIds": ["INBOX", "UNREAD"],
            },
        ).execute()

    # ------------------------------------------------------------------
    # Body extraction
    # ------------------------------------------------------------------

    def _extract_body(self, payload: dict) -> str:
        mime = payload.get("mimeType", "")
        data = payload.get("body", {}).get("data", "")

        if mime == "text/plain" and data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

        if mime == "text/html" and data:
            html = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
            converter = html2text.HTML2Text()
            converter.ignore_links = True
            converter.ignore_images = True
            return converter.handle(html)

        # Recurse into multipart
        for part in payload.get("parts", []):
            body = self._extract_body(part)
            if body:
                return body
        return ""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _is_automated(self, email: dict) -> bool:
        headers = email.get("raw_headers", {})
        return bool(_AUTOMATED_HEADERS & set(headers.keys()))


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------

def _extract_email(header: str) -> str:
    match = re.search(r"<(.+?)>", header)
    return match.group(1).strip() if match else header.strip()


def _extract_name(header: str) -> str:
    match = re.match(r"^(.+?)\s*<", header)
    if match:
        return match.group(1).strip().strip('"')
    return _extract_email(header)
