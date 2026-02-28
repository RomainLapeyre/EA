"""Notion API client — fetch relevant context pages or database records."""

import logging
import os

logger = logging.getLogger(__name__)

try:
    from notion_client import Client as NotionSDKClient
    from notion_client.errors import APIResponseError
    _NOTION_AVAILABLE = True
except ImportError:
    _NOTION_AVAILABLE = False


class NotionContextClient:
    """Fetch context from a Notion workspace.

    Two modes are supported (controlled via environment variables):

    1. **Database mode** (``NOTION_DATABASE_ID``): Queries a Notion database
       whose entries act as a knowledge-base for the assistant.  Each record's
       title + rich-text properties are extracted as context.

    2. **Page mode** (``NOTION_PAGE_IDS``): Reads a comma-separated list of
       specific page IDs and returns their plain-text content.
    """

    def __init__(self, api_key: str, database_id: str | None = None, page_ids: str | None = None):
        if not _NOTION_AVAILABLE:
            raise RuntimeError("notion-client is not installed. Run: pip install notion-client")
        self.client = NotionSDKClient(auth=api_key)
        self.database_id = database_id
        self.page_ids: list[str] = [p.strip() for p in (page_ids or "").split(",") if p.strip()]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_relevant_context(self, query: str = "") -> str:
        """Return a text block of relevant Notion content for *query*."""
        sections: list[str] = []

        if self.database_id:
            sections.append(self._fetch_database_context(query))

        for page_id in self.page_ids:
            sections.append(self._fetch_page_context(page_id))

        combined = "\n\n".join(s for s in sections if s)
        if not combined:
            logger.debug("No Notion context found.")
        return combined

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch_database_context(self, query: str) -> str:
        """Query the configured Notion database and return formatted text."""
        try:
            response = self.client.databases.query(
                database_id=self.database_id,
                page_size=5,
            )
            rows = response.get("results", [])
            if not rows:
                return ""

            lines = ["=== Notion Knowledge Base ==="]
            for page in rows:
                title = _extract_title(page)
                body = self._fetch_page_blocks(page["id"])
                if title or body:
                    lines.append(f"\n## {title}\n{body}")
            return "\n".join(lines)
        except Exception as exc:
            logger.warning("Notion database query failed: %s", exc)
            return ""

    def _fetch_page_context(self, page_id: str) -> str:
        """Fetch and format the content of a single Notion page."""
        try:
            page = self.client.pages.retrieve(page_id=page_id)
            title = _extract_title(page)
            body = self._fetch_page_blocks(page_id)
            return f"=== {title} ===\n{body}" if (title or body) else ""
        except Exception as exc:
            logger.warning("Notion page %s fetch failed: %s", page_id, exc)
            return ""

    def _fetch_page_blocks(self, page_id: str) -> str:
        """Return the plain text content of a page's blocks."""
        try:
            response = self.client.blocks.children.list(block_id=page_id, page_size=50)
            lines = []
            for block in response.get("results", []):
                text = _block_to_text(block)
                if text:
                    lines.append(text)
            return "\n".join(lines)
        except Exception as exc:
            logger.warning("Could not fetch blocks for page %s: %s", page_id, exc)
            return ""


# ------------------------------------------------------------------
# Notion data helpers
# ------------------------------------------------------------------

def _extract_title(page: dict) -> str:
    props = page.get("properties", {})
    for prop in props.values():
        if prop.get("type") == "title":
            parts = prop.get("title", [])
            return "".join(p.get("plain_text", "") for p in parts)
    return ""


def _rich_text_to_str(rich_text_list: list) -> str:
    return "".join(item.get("plain_text", "") for item in rich_text_list)


def _block_to_text(block: dict) -> str:
    block_type = block.get("type", "")
    content = block.get(block_type, {})
    rich_text = content.get("rich_text", [])
    text = _rich_text_to_str(rich_text)

    prefixes = {
        "heading_1": "# ",
        "heading_2": "## ",
        "heading_3": "### ",
        "bulleted_list_item": "• ",
        "numbered_list_item": "1. ",
        "to_do": "[ ] ",
        "quote": "> ",
        "code": "```\n",
    }
    prefix = prefixes.get(block_type, "")
    suffix = "\n```" if block_type == "code" else ""
    return f"{prefix}{text}{suffix}" if text else ""
