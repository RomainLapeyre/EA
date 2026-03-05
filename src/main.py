#!/usr/bin/env python3
"""Executive AI Email Assistant — entry point.

Run via GitHub Actions on a schedule, or locally with environment variables set.
"""

import logging
import os
import sys

from ai_assistant import AIAssistant
from ashby_context import AshbyContextClient
from calendar_context import CalendarContextClient
from gmail_client import GmailClient
from hubspot_context import HubSpotContextClient
from notion_context import NotionContextClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("ea")


def _require(var: str) -> str:
    value = os.environ.get(var)
    if not value:
        logger.error("Required environment variable %s is not set.", var)
        sys.exit(1)
    return value


def main() -> None:
    # ------------------------------------------------------------------
    # Initialise clients
    # ------------------------------------------------------------------
    logger.info("Initialising Gmail client…")
    gmail = GmailClient(
        client_id=_require("GMAIL_CLIENT_ID"),
        client_secret=_require("GMAIL_CLIENT_SECRET"),
        refresh_token=_require("GMAIL_REFRESH_TOKEN"),
    )

    logger.info("Initialising AI assistant…")
    ai = AIAssistant(api_key=_require("ANTHROPIC_API_KEY"))

    notion: NotionContextClient | None = None
    if os.environ.get("NOTION_API_KEY"):
        logger.info("Initialising Notion client…")
        notion = NotionContextClient(
            api_key=os.environ["NOTION_API_KEY"],
            database_id=os.environ.get("NOTION_DATABASE_ID"),
            page_ids=os.environ.get("NOTION_PAGE_IDS"),
        )

    hubspot: HubSpotContextClient | None = None
    if os.environ.get("HUBSPOT_ACCESS_TOKEN"):
        logger.info("Initialising HubSpot client…")
        hubspot = HubSpotContextClient(access_token=os.environ["HUBSPOT_ACCESS_TOKEN"])

    calendar: CalendarContextClient | None = None
    if os.environ.get("GOOGLE_CALENDAR_ENABLED", "false").lower() == "true":
        logger.info("Initialising Google Calendar client…")
        calendar = CalendarContextClient(
            client_id=_require("GMAIL_CLIENT_ID"),
            client_secret=_require("GMAIL_CLIENT_SECRET"),
            refresh_token=_require("GMAIL_REFRESH_TOKEN"),
        )

    ashby: AshbyContextClient | None = None
    if os.environ.get("ASHBY_API_KEY"):
        logger.info("Initialising Ashby client…")
        ashby = AshbyContextClient(api_key=os.environ["ASHBY_API_KEY"])

    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"

    if dry_run:
        logger.info("DRY RUN mode — no drafts will be created.")

    # Fetch the Gmail signature once; appended to every draft.
    signature = gmail.get_signature()
    if signature:
        logger.info("Gmail signature loaded (%d chars).", len(signature))
    else:
        logger.info("No Gmail signature found — drafts will have no sign-off block.")

    # ------------------------------------------------------------------
    # Process emails
    # ------------------------------------------------------------------
    logger.info("Fetching all unprocessed primary inbox emails without existing drafts…")
    emails = gmail.get_unprocessed_emails()
    logger.info("Found %d email(s) to process.", len(emails))

    processed = 0
    errors = 0

    for email in emails:
        subject = email["subject"][:70]
        sender = email["from_email"]
        logger.info("Processing: '%s' from %s", subject, sender)

        try:
            # Classify before doing any work
            classification = ai.classify_email(email)
            if classification == "skip":
                logger.info("Skipping (newsletter/cold outreach): '%s'", subject)
                if not dry_run:
                    gmail.archive_as_newsletter(email["id"])
                processed += 1
                continue

            # Gather thread history for better context
            thread_history = gmail.get_thread_history(email["thread_id"], email["id"])

            # Gather optional external context
            notion_context = ""
            if notion:
                query = f"{email['subject']} {email['body'][:400]}"
                notion_context = notion.get_relevant_context(query=query)

            hubspot_context = ""
            if hubspot:
                hubspot_context = hubspot.get_contact_context(sender)

            ashby_context = ""
            if ashby:
                ashby_context = ashby.get_candidate_context(sender)

            calendar_context = ""
            free_slots_context = ""
            if classification == "meeting":
                logger.info("Meeting request detected: '%s'", subject)
                if calendar:
                    sched = ai.persona.get("scheduling", {})
                    free_slots_context = calendar.get_free_slots(
                        timezone=sched.get("timezone", "America/New_York"),
                        working_hours_start=int(sched.get("working_hours_start", 9)),
                        working_hours_end=int(sched.get("working_hours_end", 18)),
                        slot_duration_minutes=int(sched.get("slot_duration_minutes", 30)),
                        lookahead_days=int(sched.get("lookahead_days", 7)),
                        slots_to_propose=int(sched.get("slots_to_propose", 3)),
                    )
                    logger.info("Free slots context: %d chars", len(free_slots_context))
                    calendar_context = calendar.get_upcoming_context()
                else:
                    logger.warning(
                        "Meeting request detected but GOOGLE_CALENDAR_ENABLED is not set."
                    )
                    free_slots_context = (
                        "=== Scheduling note ===\n"
                        "Calendar integration is not enabled. "
                        "[ROMAIN TO VERIFY AVAILABILITY before confirming any times.]"
                    )
            elif calendar:
                calendar_context = calendar.get_upcoming_context()

            # Generate draft
            draft_body = ai.generate_draft_reply(
                email=email,
                thread_history=thread_history,
                notion_context=notion_context,
                hubspot_context=hubspot_context,
                ashby_context=ashby_context,
                calendar_context=calendar_context,
                free_slots_context=free_slots_context,
            )

            # Download any PDF attachments from the original email so they
            # can be forwarded with the draft reply.
            pdf_attachments: list[dict] = []
            for att_meta in email.get("attachments", []):
                try:
                    data = gmail.get_attachment(email["id"], att_meta["attachment_id"])
                    pdf_attachments.append({"filename": att_meta["filename"], "data": data})
                    logger.info("Downloaded attachment '%s'", att_meta["filename"])
                except Exception:
                    logger.warning("Could not download attachment '%s'", att_meta["filename"])

            if dry_run:
                sig_preview = f"\n\n-- \n{signature}" if signature else ""
                att_note = (
                    f"\n[Attachments: {', '.join(a['filename'] for a in pdf_attachments)}]"
                    if pdf_attachments else ""
                )
                print(f"\n{'='*60}\nDRAFT for: {subject}\n{'='*60}\n{draft_body}{sig_preview}{att_note}\n")
            else:
                gmail.create_draft_reply(
                    original_email=email,
                    draft_body=draft_body,
                    signature=signature,
                    attachments=pdf_attachments or None,
                )
                gmail.mark_as_processed(email["id"])

            processed += 1
            logger.info("Done: '%s'", subject)

        except Exception:
            errors += 1
            logger.exception("Failed to process email '%s'", subject)

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    logger.info(
        "Finished. Processed: %d  |  Errors: %d  |  Dry-run: %s",
        processed,
        errors,
        dry_run,
    )

    if errors > 0 and processed == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
