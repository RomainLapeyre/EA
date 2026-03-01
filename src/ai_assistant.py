"""Claude-powered email analysis and draft generation."""

import logging
import os
from pathlib import Path

import anthropic
import yaml

logger = logging.getLogger(__name__)

_MODEL = "claude-opus-4-6"
_CLASSIFY_MODEL = "claude-haiku-4-5-20251001"
_MAX_TOKENS = 1024

# Path to the user-editable persona file
_PERSONA_FILE = Path(__file__).parent.parent / "config" / "persona.yaml"

_SYSTEM_PROMPT_TEMPLATE = """\
You are an executive assistant for {name}, {role} at {company}.

Your job is to draft professional, ready-to-send email replies on their behalf.

--- PERSONA ---
{persona_notes}

--- INSTRUCTIONS ---
• Write in first-person as {name}.
• Match their communication style: {tone}.
• Be concise — get to the point quickly.
• Address every question or request in the email.
• Never invent facts, commitments, or promises not clearly implied by the context.
• If the email is ambiguous or requires a decision you cannot make, include a clear
  placeholder like [YOUR DECISION HERE] and briefly explain what is needed.
• Do NOT include a subject line — only write the email body.
• Do NOT add a preamble like "Here is a draft:" — output only the email text.
• Do NOT add any sign-off, valediction, or closing name — the sender's signature
  is appended automatically by Gmail.
"""


class AIAssistant:
    """Wraps the Anthropic Claude API to generate email draft replies."""

    def __init__(self, api_key: str):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.persona = _load_persona()

    def classify_email(self, email: dict) -> str:
        """Return 'reply' if the email warrants a draft, or 'skip' if it should be archived.

        Skipped emails are newsletters, marketing blasts, cold outreach, automated
        notifications, and any other bulk or impersonal mail.
        """
        prompt = (
            f"Subject: {email['subject']}\n"
            f"From: {email['from']}\n\n"
            f"{email['body'][:1000]}\n\n"
            "Is this email a personal/direct message that warrants a reply, "
            "or is it a newsletter, cold outreach, sales pitch, marketing email, "
            "or automated notification that should be archived without a reply?\n"
            "Reply with exactly one word: 'reply' or 'skip'."
        )
        response = self.client.messages.create(
            model=_CLASSIFY_MODEL,
            max_tokens=10,
            messages=[{"role": "user", "content": prompt}],
        )
        result = response.content[0].text.strip().lower()
        return "reply" if result.startswith("reply") else "skip"

    def generate_draft_reply(
        self,
        email: dict,
        thread_history: str = "",
        notion_context: str = "",
        hubspot_context: str = "",
        ashby_context: str = "",
        calendar_context: str = "",
    ) -> str:
        """Return a draft reply body for *email*."""
        system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(
            name=self.persona["name"],
            role=self.persona["role"],
            company=self.persona["company"],
            tone=self.persona["tone"],
            persona_notes=self.persona.get("notes", ""),
        )

        user_content = _build_user_message(
            email=email,
            thread_history=thread_history,
            notion_context=notion_context,
            hubspot_context=hubspot_context,
            ashby_context=ashby_context,
            calendar_context=calendar_context,
        )

        logger.debug("Sending email to Claude for drafting (subject: %s)", email["subject"])

        response = self.client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        )

        draft = response.content[0].text.strip()
        logger.debug("Draft generated (%d chars)", len(draft))
        return draft


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _build_user_message(
    email: dict,
    thread_history: str,
    notion_context: str,
    hubspot_context: str,
    ashby_context: str = "",
    calendar_context: str = "",
) -> str:
    parts = []

    if thread_history:
        parts.append(f"=== Earlier messages in this thread ===\n{thread_history}\n")

    parts.append(
        f"=== Incoming email ===\n"
        f"From: {email['from']}\n"
        f"Date: {email['date']}\n"
        f"Subject: {email['subject']}\n\n"
        f"{email['body']}"
    )

    if hubspot_context:
        parts.append(f"\n{hubspot_context}")

    if ashby_context:
        parts.append(f"\n{ashby_context}")

    if calendar_context:
        parts.append(f"\n{calendar_context}")

    if notion_context:
        parts.append(f"\n{notion_context}")

    parts.append(
        "\nPlease draft a reply to the email above. "
        "Use the CRM and knowledge-base context where relevant, but do not force it in."
    )

    return "\n\n".join(parts)


def _load_persona() -> dict:
    """Load persona from config/persona.yaml, falling back to env vars."""
    defaults = {
        "name": os.environ.get("EXECUTIVE_NAME", "Alex"),
        "role": os.environ.get("EXECUTIVE_ROLE", "Executive"),
        "company": os.environ.get("EXECUTIVE_COMPANY", "Acme Corp"),
        "tone": os.environ.get("EMAIL_TONE", "professional and concise"),
        "sign_off": os.environ.get("EMAIL_SIGN_OFF", "Best,\n{name}"),
        "notes": "",
    }

    if _PERSONA_FILE.exists():
        try:
            with _PERSONA_FILE.open() as f:
                data = yaml.safe_load(f) or {}
            for key, val in data.items():
                if val and str(val).strip() not in ("", "~", "null"):
                    defaults[key] = val
            logger.info("Persona loaded from %s", _PERSONA_FILE)
        except Exception as exc:
            logger.warning("Could not parse persona.yaml: %s — using defaults", exc)

    # Resolve {name} placeholder in sign_off
    defaults["sign_off"] = defaults["sign_off"].format(name=defaults["name"])
    return defaults
