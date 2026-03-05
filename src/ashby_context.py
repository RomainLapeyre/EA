"""Ashby ATS context — fetch candidate info by email address."""

import logging

import requests

logger = logging.getLogger(__name__)

_BASE = "https://api.ashbyhq.com"
_TIMEOUT = 10


class AshbyContextClient:
    """Fetch candidate context from Ashby ATS for a given email address.

    Requires an API key from Ashby → Settings → Integrations → API Keys.
    Store it as the ``ASHBY_API_KEY`` environment variable / GitHub Secret.
    Ashby uses HTTP Basic auth: API key as the username, empty password.
    """

    def __init__(self, api_key: str):
        self.session = requests.Session()
        self.session.auth = (api_key, "")

    def get_candidate_context(self, email: str) -> str:
        """Return a formatted text block with Ashby candidate info for *email*."""
        candidate = self._find_candidate(email)
        if not candidate:
            logger.debug("No Ashby candidate found for %s", email)
            return ""

        name = candidate.get("name", "")
        sections = [f"=== Ashby Candidate: {name or email} ==="]

        for app in candidate.get("applications", [])[:3]:
            job_title = app.get("job", {}).get("title", "Unknown role")
            stage = app.get("currentInterviewStageName", "")
            status = app.get("status", "")
            line = f"  Applied for: {job_title}"
            if stage:
                line += f" — Stage: {stage}"
            if status:
                line += f" [{status}]"
            sections.append(line)

        return "\n".join(sections)

    def _find_candidate(self, email: str) -> dict | None:
        try:
            resp = self.session.post(
                f"{_BASE}/candidate.search",
                json={"email": email},
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
            return results[0] if results else None
        except Exception as exc:
            logger.warning("Ashby candidate search failed for %s: %s", email, exc)
            return None
