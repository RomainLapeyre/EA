"""HubSpot CRM client — fetch contact, company, and deal context."""

import logging

import requests

logger = logging.getLogger(__name__)

_BASE = "https://api.hubapi.com"
_TIMEOUT = 10


class HubSpotContextClient:
    """Fetch CRM context from HubSpot for a given email address.

    Requires a **Private App** access token (``HUBSPOT_ACCESS_TOKEN``).
    Create one at: HubSpot → Settings → Integrations → Private Apps.
    Required scopes: ``crm.objects.contacts.read``,
    ``crm.objects.companies.read``, ``crm.objects.deals.read``.
    """

    def __init__(self, access_token: str):
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            }
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_contact_context(self, email: str) -> str:
        """Return a formatted text block with CRM context for *email*."""
        contact = self._find_contact(email)
        if not contact:
            logger.debug("No HubSpot contact found for %s", email)
            return ""

        contact_id = contact["id"]
        props = contact.get("properties", {})
        name = f"{props.get('firstname', '')} {props.get('lastname', '')}".strip()
        job_title = props.get("jobtitle", "")
        phone = props.get("phone", "")
        lifecycle = props.get("lifecyclestage", "")
        notes_from_hs = props.get("hs_content_membership_notes", "")

        sections = [f"=== HubSpot Contact: {name or email} ==="]
        if job_title:
            sections.append(f"Title: {job_title}")
        if phone:
            sections.append(f"Phone: {phone}")
        if lifecycle:
            sections.append(f"Lifecycle stage: {lifecycle}")

        company_name = self._get_associated_company(contact_id)
        if company_name:
            sections.append(f"Company: {company_name}")

        deals = self._get_associated_deals(contact_id)
        if deals:
            sections.append("\nOpen / recent deals:")
            for deal in deals[:3]:
                sections.append(f"  • {deal}")

        recent_notes = self._get_recent_notes(contact_id)
        if recent_notes:
            sections.append("\nRecent CRM notes:")
            for note in recent_notes[:3]:
                sections.append(f"  - {note}")

        return "\n".join(sections)

    # ------------------------------------------------------------------
    # HubSpot API calls
    # ------------------------------------------------------------------

    def _find_contact(self, email: str) -> dict | None:
        url = f"{_BASE}/crm/v3/objects/contacts/search"
        payload = {
            "filterGroups": [
                {
                    "filters": [
                        {"propertyName": "email", "operator": "EQ", "value": email}
                    ]
                }
            ],
            "properties": [
                "firstname", "lastname", "jobtitle", "phone",
                "lifecyclestage", "hs_content_membership_notes",
            ],
        }
        try:
            resp = self.session.post(url, json=payload, timeout=_TIMEOUT)
            resp.raise_for_status()
            results = resp.json().get("results", [])
            return results[0] if results else None
        except Exception as exc:
            logger.warning("HubSpot contact search failed for %s: %s", email, exc)
            return None

    def _get_associated_company(self, contact_id: str) -> str:
        url = f"{_BASE}/crm/v3/objects/contacts/{contact_id}/associations/companies"
        try:
            resp = self.session.get(url, timeout=_TIMEOUT)
            resp.raise_for_status()
            results = resp.json().get("results", [])
            if not results:
                return ""
            company_id = results[0]["id"]
            c_resp = self.session.get(
                f"{_BASE}/crm/v3/objects/companies/{company_id}",
                params={"properties": "name"},
                timeout=_TIMEOUT,
            )
            c_resp.raise_for_status()
            return c_resp.json().get("properties", {}).get("name", "")
        except Exception as exc:
            logger.warning("HubSpot company fetch failed for contact %s: %s", contact_id, exc)
            return ""

    def _get_associated_deals(self, contact_id: str) -> list[str]:
        url = f"{_BASE}/crm/v3/objects/contacts/{contact_id}/associations/deals"
        try:
            resp = self.session.get(url, timeout=_TIMEOUT)
            resp.raise_for_status()
            deal_ids = [r["id"] for r in resp.json().get("results", [])][:3]
            summaries = []
            for deal_id in deal_ids:
                d_resp = self.session.get(
                    f"{_BASE}/crm/v3/objects/deals/{deal_id}",
                    params={"properties": "dealname,dealstage,amount,closedate"},
                    timeout=_TIMEOUT,
                )
                d_resp.raise_for_status()
                p = d_resp.json().get("properties", {})
                name = p.get("dealname", "Unnamed deal")
                stage = p.get("dealstage", "")
                amount = p.get("amount", "")
                close = p.get("closedate", "")
                line = name
                if stage:
                    line += f" [{stage}]"
                if amount:
                    line += f" — ${amount}"
                if close:
                    line += f" (closes {close[:10]})"
                summaries.append(line)
            return summaries
        except Exception as exc:
            logger.warning("HubSpot deals fetch failed for contact %s: %s", contact_id, exc)
            return []

    def _get_recent_notes(self, contact_id: str) -> list[str]:
        url = f"{_BASE}/crm/v3/objects/notes/search"
        payload = {
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": "associations.contact",
                            "operator": "EQ",
                            "value": contact_id,
                        }
                    ]
                }
            ],
            "properties": ["hs_note_body", "hs_timestamp"],
            "sorts": [{"propertyName": "hs_timestamp", "direction": "DESCENDING"}],
            "limit": 3,
        }
        try:
            resp = self.session.post(url, json=payload, timeout=_TIMEOUT)
            resp.raise_for_status()
            notes = []
            for result in resp.json().get("results", []):
                body = result.get("properties", {}).get("hs_note_body", "").strip()
                if body:
                    notes.append(body[:200])
            return notes
        except Exception as exc:
            logger.warning("HubSpot notes fetch failed for contact %s: %s", contact_id, exc)
            return []
