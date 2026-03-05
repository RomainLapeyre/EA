# Skill: add-context-source

Add a new external data source (CRM, ATS, calendar, wiki, etc.) that the EA
pulls context from when drafting replies.

## What this does

Creates a new `src/<name>_context.py` client, wires it into `main.py` and
`ai_assistant.py`, and adds the required env var to the GitHub Actions workflow.

## Prompt

```
I want to add a new context source to the EA email assistant.

Name: $ARGUMENTS

Follow the same pattern as the existing clients in src/:
- hubspot_context.py  (CRM — search by email, return formatted text)
- ashby_context.py    (ATS — search by email, return formatted text)
- notion_context.py   (knowledge base — keyword search)
- calendar_context.py (Google Calendar — upcoming events)

Steps:
1. Read src/hubspot_context.py to understand the pattern.
2. Create src/<name>_context.py with a <Name>ContextClient class that:
   - Takes credentials/API key in __init__
   - Has a get_<entity>_context(email) -> str method
   - Returns "" if nothing is found (never raises)
3. Add the client to main.py (optional, gated by env var).
4. Pass the context string into ai_assistant.generate_draft_reply() and
   _build_user_message() with a new keyword argument.
5. Add the env var to .github/workflows/email-assistant.yml.
6. Commit with a descriptive message.
```
