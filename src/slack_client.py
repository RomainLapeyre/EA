"""Slack outbound notifications — post EA run summaries via Incoming Webhook.

Set ``SLACK_WEBHOOK_URL`` to an Incoming Webhook URL created in your Slack app.
Create one at: Slack API → Your App → Incoming Webhooks → Add New Webhook.
No server required; this is a simple HTTPS POST.
"""

import logging

import requests

logger = logging.getLogger(__name__)

_TIMEOUT = 10


class SlackNotifier:
    """Post EA run summaries and alerts to a Slack channel."""

    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    def post_run_summary(
        self,
        drafted: int,
        archived: int,
        errors: int,
        memories_added: int = 0,
        dry_run: bool = False,
    ) -> None:
        """Post a concise summary block after each EA run."""
        status = "✅" if errors == 0 else "⚠️"
        mode = " _(dry run)_" if dry_run else ""
        lines = [
            f"{status} *EA run complete*{mode}",
            f"• Drafted: *{drafted}*  |  Archived: *{archived}*  |  Errors: *{errors}*",
        ]
        if memories_added:
            lines.append(f"• 🧠 Stored *{memories_added}* new memory item(s) in Notion")
        self._post("\n".join(lines))

    def post_message(self, text: str) -> None:
        """Post an arbitrary text message."""
        self._post(text)

    # ------------------------------------------------------------------

    def _post(self, text: str) -> None:
        try:
            resp = requests.post(
                self.webhook_url,
                json={"text": text},
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            logger.debug("Slack notification sent.")
        except Exception as exc:
            logger.warning("Slack webhook failed: %s", exc)
