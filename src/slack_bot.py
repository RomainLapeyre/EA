"""Slack bot — receives slash commands from Romain and routes them.

Deploy this as a separate persistent service (e.g. Fly.io, Railway).
It is NOT part of the GitHub Actions EA run.

Required environment variables:
    SLACK_SIGNING_SECRET   Slack app signing secret (Settings → Basic Information)
    SLACK_ALLOWED_USER_ID  Your Slack user ID — only this user can control the EA
    GITHUB_TOKEN           Personal access token with repo + workflow scope
    GITHUB_REPO            owner/repo  e.g. "RomainLapeyre/EA"
    NOTION_API_KEY         Notion integration token
    NOTION_MEMORY_PAGE_ID  Private EA Memory root page
    NOTION_SKILLS_PAGE_ID  Private EA Skills root page

Supported commands (all via /ea):
    /ea run          Trigger an email processing run via GitHub Actions
    /ea dry-run      Trigger a dry run (no drafts created)
    /ea learn [fact] Append a fact to EA Memory in Notion
    /ea skill [desc] Append a skill draft to EA Skills in Notion (for review)
    /ea help         Show this help

Slack app setup:
    1. Create app at api.slack.com/apps
    2. Enable Slash Commands → /ea → Request URL: https://your-host/slack/command
    3. Install app to Gorgias workspace
    4. Copy Signing Secret → SLACK_SIGNING_SECRET
    5. Find your user ID: Slack → Profile → ⋯ → Copy member ID → SLACK_ALLOWED_USER_ID
"""

import hashlib
import hmac
import logging
import os
import sys
import time

import requests
from flask import Flask, Response, abort, jsonify, request

# ---------------------------------------------------------------------------
# Add src/ to path so notion_memory can be imported when run standalone
sys.path.insert(0, os.path.dirname(__file__))
from notion_memory import NotionMemoryClient  # noqa: E402

# ---------------------------------------------------------------------------

app = Flask(__name__)
logger = logging.getLogger(__name__)

_SIGNING_SECRET = os.environ.get("SLACK_SIGNING_SECRET", "")
_ALLOWED_USER = os.environ.get("SLACK_ALLOWED_USER_ID", "")
_GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
_GITHUB_REPO = os.environ.get("GITHUB_REPO", "")  # "owner/repo"

_WORKFLOW_FILE = "email-assistant.yml"
_GITHUB_BRANCH = "main"


# ---------------------------------------------------------------------------
# Slack request verification
# ---------------------------------------------------------------------------

def _verify_slack_request(req: "request") -> bool:
    """Return True if the request is genuinely from Slack (HMAC-SHA256)."""
    ts = req.headers.get("X-Slack-Request-Timestamp", "")
    sig = req.headers.get("X-Slack-Signature", "")
    if not ts or not sig:
        return False
    try:
        if abs(time.time() - float(ts)) > 300:
            return False  # replay-attack guard
    except ValueError:
        return False

    base = f"v0:{ts}:{req.get_data(as_text=True)}"
    expected = "v0=" + hmac.new(
        _SIGNING_SECRET.encode(),
        base.encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, sig)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/slack/command", methods=["POST"])
def slack_command() -> Response:
    if not _verify_slack_request(request):
        logger.warning("Slack signature verification failed")
        abort(403)

    user_id = request.form.get("user_id", "")
    if _ALLOWED_USER and user_id != _ALLOWED_USER:
        return jsonify({"text": "Not authorised."}), 200

    text = request.form.get("text", "").strip()
    return _handle_ea_command(text)


@app.route("/health", methods=["GET"])
def health() -> Response:
    return "ok", 200


# ---------------------------------------------------------------------------
# Command routing
# ---------------------------------------------------------------------------

def _handle_ea_command(text: str) -> Response:
    parts = text.split(" ", 1)
    sub = parts[0].lower() if parts else ""
    args = parts[1].strip() if len(parts) > 1 else ""

    if sub == "run":
        ok = _trigger_workflow(dry_run=False)
        msg = "🚀 Run triggered — check GitHub Actions for progress." if ok \
            else "⚠️ Could not trigger run — check GITHUB_TOKEN / GITHUB_REPO."
        return jsonify({"text": msg}), 200

    if sub == "dry-run":
        ok = _trigger_workflow(dry_run=True)
        msg = "🔍 Dry run triggered — no drafts will be created." if ok \
            else "⚠️ Could not trigger dry run."
        return jsonify({"text": msg}), 200

    if sub == "learn":
        if not args:
            return jsonify({"text": "Usage: `/ea learn [fact to remember]`"}), 200
        _notion_write_memory(args)
        return jsonify({"text": f"🧠 Stored: _{args}_"}), 200

    if sub == "skill":
        if not args:
            return jsonify({"text": "Usage: `/ea skill [skill description]`"}), 200
        _notion_write_skill(args)
        return jsonify(
            {"text": f"📚 Skill draft saved to Notion (📝 Drafts) for your review: _{args}_"}
        ), 200

    # Default / help
    return jsonify({"text": _HELP_TEXT}), 200


_HELP_TEXT = (
    "*EA slash commands:*\n"
    "• `/ea run` — trigger an email processing run now\n"
    "• `/ea dry-run` — run without creating drafts\n"
    "• `/ea learn [fact]` — add a fact to EA Memory in Notion\n"
    "• `/ea skill [desc]` — propose a new skill (saved to Notion Drafts for review)\n"
    "• `/ea help` — show this message"
)


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

def _trigger_workflow(dry_run: bool = False) -> bool:
    """Dispatch the GitHub Actions workflow via the REST API."""
    if not _GITHUB_TOKEN or not _GITHUB_REPO:
        logger.error("GITHUB_TOKEN or GITHUB_REPO not set")
        return False
    url = (
        f"https://api.github.com/repos/{_GITHUB_REPO}"
        f"/actions/workflows/{_WORKFLOW_FILE}/dispatches"
    )
    headers = {
        "Authorization": f"token {_GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    payload = {
        "ref": _GITHUB_BRANCH,
        "inputs": {"dry_run": "true" if dry_run else "false"},
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        resp.raise_for_status()
        logger.info("GitHub workflow dispatched (dry_run=%s)", dry_run)
        return True
    except Exception as exc:
        logger.error("GitHub dispatch failed: %s", exc)
        return False


def _notion_write_memory(fact: str) -> None:
    client = _notion_client()
    if client:
        client.append_memory("Misc", fact)


def _notion_write_skill(desc: str) -> None:
    client = _notion_client()
    if client:
        client.append_skill_draft("Manual", desc)


def _notion_client() -> NotionMemoryClient | None:
    api_key = os.environ.get("NOTION_API_KEY", "")
    if not api_key:
        logger.warning("NOTION_API_KEY not set")
        return None
    return NotionMemoryClient(
        api_key=api_key,
        skills_page_id=os.environ.get("NOTION_SKILLS_PAGE_ID"),
        memory_page_id=os.environ.get("NOTION_MEMORY_PAGE_ID"),
    )


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
