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
from granola_context import GranolaContextClient
from hubspot_context import HubSpotContextClient
from investor_crm import InvestorCRMClient
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

    granola: GranolaContextClient | None = None
    if os.environ.get("GRANOLA_REFRESH_TOKEN"):
        logger.info("Initialising Granola client…")
        granola = GranolaContextClient(refresh_token=os.environ["GRANOLA_REFRESH_TOKEN"])

    ashby: AshbyContextClient | None = None
    if os.environ.get("ASHBY_API_KEY"):
        logger.info("Initialising Ashby client…")
        ashby = AshbyContextClient(api_key=os.environ["ASHBY_API_KEY"])

    # Fundraising CRM database ID — hardcoded from the Notion page URL anchor.
    # Override via NOTION_INVESTOR_CRM_ID if you ever point this at a different DB.
    _INVESTOR_CRM_DB_ID = os.environ.get(
        "NOTION_INVESTOR_CRM_ID", "2a9ef5b9e6b74181b507c98b2c859eae"
    )
    investor_crm: InvestorCRMClient | None = None
    if os.environ.get("NOTION_API_KEY"):
        logger.info("Initialising Investor CRM client…")
        investor_crm = InvestorCRMClient(
            notion_api_key=os.environ["NOTION_API_KEY"],
            crm_database_id=_INVESTOR_CRM_DB_ID,
        )

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

            granola_context = ""
            if granola:
                from_name = email.get("from_name", "")
                granola_context = granola.get_meeting_context(sender, from_name)
                if granola_context:
                    logger.info("Granola context found for %s", sender)

            ashby_context = ""
            is_candidate = False
            if ashby:
                ashby_context = ashby.get_candidate_context(sender)
                if ashby_context:
                    is_candidate = True
                    logger.info("Ashby candidate found for %s — will tag as recruiting", sender)

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
                granola_context=granola_context,
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

            # Detect if user was not a direct To recipient (e.g. CC'd or BCC'd)
            is_unknown_recipient = gmail.my_email.lower() not in email.get("to", "").lower()

            if dry_run:
                sig_preview = f"\n\n-- \n{signature}" if signature else ""
                att_note = (
                    f"\n[Attachments: {', '.join(a['filename'] for a in pdf_attachments)}]"
                    if pdf_attachments else ""
                )
                recruiting_note = "\n[Tagged: EA/Recruiting]" if is_candidate else ""
                unknown_note = "\n[Tagged: EA/Unknown]" if is_unknown_recipient else ""
                print(f"\n{'='*60}\nDRAFT for: {subject}\n{'='*60}\n{draft_body}{sig_preview}{att_note}{recruiting_note}{unknown_note}\n")
            else:
                gmail.create_draft_reply(
                    original_email=email,
                    draft_body=draft_body,
                    signature=signature,
                    attachments=pdf_attachments or None,
                )
                if is_candidate:
                    gmail.tag_as_recruiting(email["id"])
                if is_unknown_recipient:
                    gmail.tag_as_unknown(email["id"])
                gmail.mark_as_processed(email["id"])

            # ------------------------------------------------------------------
            # Case study processing — if the email is a candidate case study
            # submission, enrich the calendar invite + HubSpot contact.
            # ------------------------------------------------------------------
            if ashby and is_candidate:
                try:
                    cs_meta = ai.detect_case_study(email)
                    if cs_meta.get("is_case_study"):
                        cs_url = cs_meta.get("case_study_url", "")
                        logger.info(
                            "Case study detected from %s (url: %s)", sender, cs_url or "attachment only"
                        )
                        # 1. Add note on Ashby candidate profile
                        if cs_url and not dry_run:
                            ashby.add_case_study_note(sender, cs_url)

                        # 2. Find calendar event + update description
                        if calendar:
                            linkedin_url = ashby.get_candidate_linkedin(sender)
                            event = calendar.find_interview_event(sender)
                            if event:
                                event_title = event.get("summary", event["id"])
                                logger.info(
                                    "Found interview event '%s' — updating with case study info.",
                                    event_title,
                                )
                                if not dry_run:
                                    calendar.update_event_with_case_study(
                                        event_id=event["id"],
                                        case_study_url=cs_url,
                                        linkedin_url=linkedin_url,
                                    )
                                else:
                                    print(
                                        f"\n[DRY RUN] Would update calendar event '{event_title}'"
                                        f"\n  Case study: {cs_url}"
                                        f"\n  LinkedIn:   {linkedin_url}"
                                        f"\n  Ashby note → {cs_url}\n"
                                    )
                            else:
                                logger.info(
                                    "No upcoming interview event found for %s.", sender
                                )
                        elif not dry_run and cs_url:
                            logger.info(
                                "Calendar not enabled — skipping event update for case study."
                            )
                except Exception:
                    logger.warning("Case study processing failed for '%s'", subject, exc_info=True)

            # ------------------------------------------------------------------
            # Investor CRM — check if sender is an investor with a positive
            # interaction and a calendar event, then sync to Notion.
            # ------------------------------------------------------------------
            if investor_crm:
                try:
                    investor_meta = ai.classify_investor_interaction(email, thread_history)
                    if investor_meta.get("is_investor"):
                        calendar_svc = calendar.service if calendar else None
                        added = investor_crm.process_email(
                            email=email,
                            investor_meta=investor_meta,
                            calendar_service=calendar_svc,
                        )
                        if added:
                            logger.info(
                                "Added/updated investor '%s' in Notion CRM.",
                                investor_meta.get("investor_name") or sender,
                            )
                except Exception:
                    logger.warning("Investor CRM check failed for '%s'", subject, exc_info=True)

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
