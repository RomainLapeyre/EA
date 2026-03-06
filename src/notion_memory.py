"""Notion-backed Skills and Memory store for the EA.

Two private Notion pages power this module:

- **EA Skills** (``NOTION_SKILLS_PAGE_ID``): sub-pages per skill category.
  Read every run for context; new skill drafts are appended for Romain to review.

- **EA Memory** (``NOTION_MEMORY_PAGE_ID``): sub-pages per memory category
  (People, Companies, Projects, Misc).  Read every run; new facts appended
  automatically after each email is processed.

Both pages should live in a private Notion space — the integration token only
needs access to those two pages, not the whole workspace.
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

try:
    from notion_client import Client as NotionSDKClient
    _NOTION_AVAILABLE = True
except ImportError:
    _NOTION_AVAILABLE = False

# Memory categories surfaced to Claude during drafting
MEMORY_CATEGORIES = ["People", "Companies", "Projects", "Preferences", "Misc"]


class NotionMemoryClient:
    """Read/write EA Skills and Memory pages in Notion."""

    def __init__(
        self,
        api_key: str,
        skills_page_id: str | None = None,
        memory_page_id: str | None = None,
    ):
        if not _NOTION_AVAILABLE:
            raise RuntimeError("notion-client is not installed. Run: pip install notion-client")
        self.client = NotionSDKClient(auth=api_key)
        self.skills_page_id = skills_page_id
        self.memory_page_id = memory_page_id

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_skills_context(self) -> str:
        """Return formatted text of all skill sub-pages (excluding Drafts)."""
        if not self.skills_page_id:
            return ""
        return self._read_page_tree(
            self.skills_page_id,
            label="EA Skills",
            skip_prefix="📝 Drafts",
        )

    def get_memory_context(self) -> str:
        """Return formatted text of all memory sub-pages."""
        if not self.memory_page_id:
            return ""
        return self._read_page_tree(self.memory_page_id, label="EA Memory")

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def append_memory(self, category: str, fact: str) -> None:
        """Append *fact* to the sub-page for *category* under the Memory page."""
        if not self.memory_page_id:
            logger.debug("No NOTION_MEMORY_PAGE_ID — skipping memory write.")
            return
        self._append_to_subpage(self.memory_page_id, category, fact)

    def append_skill_draft(self, title: str, description: str) -> None:
        """Append a proposed skill to the Drafts sub-page under Skills for review."""
        if not self.skills_page_id:
            logger.debug("No NOTION_SKILLS_PAGE_ID — skipping skill write.")
            return
        self._append_to_subpage(
            self.skills_page_id,
            "📝 Drafts (review me)",
            description,
            title=title,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _read_page_tree(
        self, root_page_id: str, label: str, skip_prefix: str | None = None
    ) -> str:
        """Read root page + all child pages and return formatted context."""
        try:
            children = self.client.blocks.children.list(
                block_id=root_page_id, page_size=50
            )
            lines = [f"=== {label} ==="]
            for block in children.get("results", []):
                if block.get("type") != "child_page":
                    continue
                title = block["child_page"]["title"]
                if skip_prefix and title.startswith(skip_prefix):
                    continue
                page_text = self._read_page_blocks(block["id"])
                if page_text:
                    lines.append(f"\n## {title}\n{page_text}")
            return "\n".join(lines) if len(lines) > 1 else ""
        except Exception as exc:
            logger.warning("Failed to read Notion page tree (%s): %s", label, exc)
            return ""

    def _read_page_blocks(self, page_id: str) -> str:
        try:
            response = self.client.blocks.children.list(
                block_id=page_id, page_size=100
            )
            lines = []
            for block in response.get("results", []):
                text = _block_to_text(block)
                if text:
                    lines.append(text)
            return "\n".join(lines)
        except Exception as exc:
            logger.warning("Failed to read blocks for page %s: %s", page_id, exc)
            return ""

    def _append_to_subpage(
        self,
        root_page_id: str,
        category: str,
        content: str,
        title: str | None = None,
    ) -> None:
        """Find or create a sub-page for *category* and append a bullet to it."""
        try:
            sub_page_id = self._find_or_create_subpage(root_page_id, category)
            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            prefix = f"{title}: " if title else ""
            entry = f"[{timestamp}] {prefix}{content}"
            self.client.blocks.children.append(
                block_id=sub_page_id,
                children=[
                    {
                        "object": "block",
                        "type": "bulleted_list_item",
                        "bulleted_list_item": {
                            "rich_text": [
                                {
                                    "type": "text",
                                    "text": {"content": entry[:2000]},
                                }
                            ]
                        },
                    }
                ],
            )
            logger.info(
                "Notion memory updated — %s / %s", category, entry[:60]
            )
        except Exception as exc:
            logger.warning("Failed to append to Notion: %s", exc)

    def _find_or_create_subpage(self, root_page_id: str, title: str) -> str:
        """Return the page ID of a child page with *title*, creating it if absent."""
        children = self.client.blocks.children.list(
            block_id=root_page_id, page_size=50
        )
        for block in children.get("results", []):
            if (
                block.get("type") == "child_page"
                and block["child_page"]["title"] == title
            ):
                return block["id"]
        new_page = self.client.pages.create(
            parent={"page_id": root_page_id},
            properties={
                "title": {
                    "title": [{"type": "text", "text": {"content": title}}]
                }
            },
        )
        logger.info("Created Notion sub-page '%s'", title)
        return new_page["id"]


# ------------------------------------------------------------------
# Block text helper (shared with notion_context)
# ------------------------------------------------------------------

def _block_to_text(block: dict) -> str:
    block_type = block.get("type", "")
    content = block.get(block_type, {})
    rich_text = content.get("rich_text", [])
    text = "".join(item.get("plain_text", "") for item in rich_text)
    prefixes = {
        "heading_1": "# ",
        "heading_2": "## ",
        "heading_3": "### ",
        "bulleted_list_item": "• ",
        "numbered_list_item": "1. ",
        "to_do": "[ ] ",
        "quote": "> ",
    }
    prefix = prefixes.get(block_type, "")
    return f"{prefix}{text}" if text else ""
