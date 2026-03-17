"""Gmail API client — fetch unread emails and create draft replies."""

import base64
import html as _html
import logging
import re
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import html2text  # used in _extract_body
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

PROCESSED_LABEL = "EA/Processed"
NEWSLETTER_LABEL = "EA/Newsletter"
RECRUITING_LABEL = "EA/Recruiting"
UNKNOWN_LABEL = "EA/Unknown"
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
        self._recruiting_label_id = self._get_or_create_label(RECRUITING_LABEL)
        self._unknown_label_id = self._get_or_create_label(UNKNOWN_LABEL)
        self.my_email = self._fetch_my_email()

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

    def _fetch_my_email(self) -> str:
        """Return the authenticated user's email address."""
        profile = self.service.users().getProfile(userId="me").execute()
        return profile["emailAddress"]

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
        """Return threads whose last message is not from the user and have no existing draft."""
        query = f"in:inbox category:primary -label:{PROCESSED_LABEL}"
        draft_thread_ids = self.get_draft_thread_ids()
        try:
            thread_ids: list[str] = []
            page_token = None
            while True:
                kwargs: dict = {"userId": "me", "q": query}
                if page_token:
                    kwargs["pageToken"] = page_token
                result = self.service.users().threads().list(**kwargs).execute()
                for ref in result.get("threads", []):
                    if ref["id"] not in draft_thread_ids:
                        thread_ids.append(ref["id"])
                page_token = result.get("nextPageToken")
                if not page_token:
                    break

            emails = []
            for tid in thread_ids:
                last_msg = self._get_last_message_if_not_mine(tid)
                if last_msg and not self._is_automated(last_msg):
                    emails.append(last_msg)
            return emails
        except HttpError as exc:
            logger.error("Error listing threads: %s", exc)
            raise

    def _get_last_message_if_not_mine(self, thread_id: str) -> dict | None:
        """Return the last message in *thread_id* if it was NOT sent by the user, else None."""
        try:
            thread = (
                self.service.users()
                .threads()
                .get(userId="me", id=thread_id, format="full")
                .execute()
            )
            messages = thread.get("messages", [])
            if not messages:
                return None
            last_msg = messages[-1]
            headers = {
                h["name"].lower(): h["value"]
                for h in last_msg["payload"].get("headers", [])
            }
            from_email = _extract_email(headers.get("from", ""))
            if from_email.lower() == self.my_email.lower():
                logger.debug("Skipping thread %s — last message is from me.", thread_id)
                return None
            return self._parse_message(last_msg["id"])
        except HttpError as exc:
            logger.warning("Could not inspect thread %s: %s", thread_id, exc)
            return None

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
            cc_header = headers.get("cc", "")
            date = headers.get("date", "")
            message_id_header = headers.get("message-id", "")

            body = self._extract_body(msg["payload"])
            attachments = self._extract_attachment_metadata(msg["payload"])

            return {
                "id": message_id,
                "thread_id": msg["threadId"],
                "message_id_header": message_id_header,
                "subject": subject,
                "from": from_header,
                "from_email": _extract_email(from_header),
                "from_name": _extract_name(from_header),
                "to": to_header,
                "cc": cc_header,
                "date": date,
                "body": body,
                "snippet": msg.get("snippet", ""),
                "raw_headers": headers,
                "attachments": attachments,
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
        """Return the raw HTML of the user's primary Gmail signature.

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
                    return send_as.get("signature", "")
            return ""
        except HttpError as exc:
            logger.warning("Could not fetch Gmail signature: %s", exc)
            return ""

    # ------------------------------------------------------------------
    # Drafts
    # ------------------------------------------------------------------

    def create_draft_reply(
        self,
        original_email: dict,
        draft_body: str,
        signature: str = "",
        attachments: list[dict] | None = None,
    ) -> str:
        """Create a Gmail draft as a reply to *original_email*.

        The draft is sent as HTML so that the signature (raw Gmail HTML) renders
        with working hyperlinks. If *signature* is provided it is appended after
        the body, separated by the conventional ``--`` delimiter.

        *attachments* is an optional list of dicts with keys ``filename`` (str)
        and ``data`` (bytes). Each entry is attached as a PDF.
        """
        body_html = _text_to_html(draft_body)
        if signature:
            html = f"{body_html}<br><br>--&nbsp;<br>{signature}"
        else:
            html = body_html

        if attachments:
            msg: MIMEMultipart | MIMEText = MIMEMultipart("mixed")
            msg.attach(MIMEText(html, "html", "utf-8"))
            for att in attachments:
                part = MIMEApplication(att["data"], _subtype="pdf")
                part.add_header(
                    "Content-Disposition", "attachment", filename=att["filename"]
                )
                msg.attach(part)
                logger.info("Attaching PDF '%s' (%d bytes)", att["filename"], len(att["data"]))
        else:
            msg = MIMEText(html, "html", "utf-8")

        # Reply All: To = sender + original To recipients (minus self)
        #            Cc = original Cc recipients (minus self)
        all_to = _merge_recipients(original_email["from"], original_email.get("to", ""), self.my_email)
        all_cc = _filter_self(original_email.get("cc", ""), self.my_email)
        msg["To"] = all_to
        if all_cc:
            msg["Cc"] = all_cc
        msg["Subject"] = (
            original_email["subject"]
            if original_email["subject"].lower().startswith("re:")
            else f"Re: {original_email['subject']}"
        )
        if original_email["message_id_header"]:
            msg["In-Reply-To"] = original_email["message_id_header"]
            msg["References"] = original_email["message_id_header"]

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

    def tag_as_recruiting(self, message_id: str) -> None:
        """Add the EA/Recruiting label to a message from a known candidate."""
        self.service.users().messages().modify(
            userId="me",
            id=message_id,
            body={"addLabelIds": [self._recruiting_label_id]},
        ).execute()

    def tag_as_unknown(self, message_id: str) -> None:
        """Add the EA/Unknown label when the user was not a direct To recipient."""
        self.service.users().messages().modify(
            userId="me",
            id=message_id,
            body={"addLabelIds": [self._unknown_label_id]},
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
    # Attachments
    # ------------------------------------------------------------------

    def _extract_attachment_metadata(self, payload: dict) -> list[dict]:
        """Return a list of PDF attachment descriptors found in *payload*.

        Each descriptor has ``filename`` and ``attachment_id`` keys.
        Inline parts (no filename) are skipped.
        """
        results: list[dict] = []
        mime = payload.get("mimeType", "")
        body = payload.get("body", {})
        filename = payload.get("filename", "")

        if filename and mime == "application/pdf" and body.get("attachmentId"):
            results.append({"filename": filename, "attachment_id": body["attachmentId"]})

        for part in payload.get("parts", []):
            results.extend(self._extract_attachment_metadata(part))

        return results

    def get_attachment(self, message_id: str, attachment_id: str) -> bytes:
        """Download and return the raw bytes of a Gmail message attachment."""
        result = (
            self.service.users()
            .messages()
            .attachments()
            .get(userId="me", messageId=message_id, id=attachment_id)
            .execute()
        )
        return base64.urlsafe_b64decode(result["data"])

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

def _text_to_html(text: str) -> str:
    """Convert a plain-text email body to minimal HTML using <br> line breaks."""
    return _html.escape(text).replace("\n", "<br>\n")


def _filter_self(recipients: str, my_email: str) -> str:
    """Return *recipients* with the user's own address removed."""
    if not recipients:
        return ""
    filtered = [
        r.strip() for r in recipients.split(",")
        if _extract_email(r).lower() != my_email.lower()
    ]
    return ", ".join(filtered)


def _merge_recipients(sender: str, original_to: str, my_email: str) -> str:
    """Build Reply-All To field: sender + original To, excluding self."""
    parts = [sender] if sender else []
    for r in original_to.split(","):
        r = r.strip()
        if r and _extract_email(r).lower() != my_email.lower():
            parts.append(r)
    return ", ".join(parts)


def _extract_email(header: str) -> str:
    match = re.search(r"<(.+?)>", header)
    return match.group(1).strip() if match else header.strip()


def _extract_name(header: str) -> str:
    match = re.match(r"^(.+?)\s*<", header)
    if match:
        return match.group(1).strip().strip('"')
    return _extract_email(header)
