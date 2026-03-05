# EA — AI Email Assistant

An AI-powered executive assistant that runs on a schedule, reads Romain's
primary Gmail inbox, classifies each email (newsletter vs reply-needed),
archives junk automatically, and creates Gmail draft replies for the rest.

## Key files

| File | Purpose |
|------|---------|
| `src/main.py` | Entry point — orchestrates all clients |
| `src/ai_assistant.py` | Claude API calls: classify + draft |
| `src/gmail_client.py` | Gmail read / draft / label / archive |
| `src/calendar_context.py` | Google Calendar context for scheduling |
| `src/hubspot_context.py` | HubSpot CRM contact context |
| `src/ashby_context.py` | Ashby ATS candidate context |
| `src/notion_context.py` | Notion knowledge-base context |
| `config/persona.yaml` | Romain's writing style and persona |
| `scripts/setup_gmail_auth.py` | One-time OAuth token generator |

## Running locally

```bash
pip install -r requirements.txt

# Dry run — shows drafts without touching Gmail
DRY_RUN=true \
GMAIL_CLIENT_ID=... \
GMAIL_CLIENT_SECRET=... \
GMAIL_REFRESH_TOKEN=... \
ANTHROPIC_API_KEY=... \
python src/main.py
```

## Architecture

Every run:
1. Fetch unread Primary inbox emails (not already labelled `EA/Processed`)
2. **Classify** each with Claude Haiku → `reply` | `skip`
3. **Skip** → label `EA/Newsletter` + archive (remove from inbox)
4. **Reply** → gather Calendar / HubSpot / Ashby / Notion context → draft with Claude Opus
5. Save draft to Gmail + label `EA/Processed`

## Persona rules (persona.yaml)

- Never add a sign-off or closing name — Gmail appends the real signature
- In French: greet with "hello", never "salut"
- Never use "Best" or any valediction
- Keep replies short and direct; avoid corporate jargon

## Adding a new context source

Use the `/add-source` command or follow `src/hubspot_context.py` as a template.

## Secrets (GitHub Actions)

| Secret / Variable | Required | Purpose |
|-------------------|----------|---------|
| `GMAIL_CLIENT_ID` | ✓ | OAuth client |
| `GMAIL_CLIENT_SECRET` | ✓ | OAuth client |
| `GMAIL_REFRESH_TOKEN` | ✓ | OAuth access |
| `ANTHROPIC_API_KEY` | ✓ | Claude API |
| `HUBSPOT_ACCESS_TOKEN` | optional | CRM context |
| `ASHBY_API_KEY` | optional | ATS context |
| `NOTION_API_KEY` | optional | KB context |
| `GOOGLE_CALENDAR_ENABLED` | optional var | Calendar context |
