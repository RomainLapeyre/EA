# AI Email Assistant

An open-source executive assistant that runs entirely on **GitHub Actions**.
It reads your unread Gmail, classifies each message, archives junk
automatically, and drafts replies using **Claude (Anthropic)** — saving them
back to **Gmail Drafts** so you always have final approval before anything is
sent.

Optional integrations enrich every draft with context from **HubSpot**,
**Ashby ATS**, **Notion**, **Google Calendar**, and **Gong**.

---

## How it works

```
4× per day (GitHub Actions cron: 7 AM / 12 PM / 5 PM / 8 PM ET)
  └─ Fetch unread Primary inbox threads (not yet labelled EA/Processed)
        └─ For each thread:
              ├─ Skip if last message was sent by you
              ├─ Classify with Claude Haiku → reply | skip
              │
              ├─ skip  → label EA/Newsletter + archive (remove from inbox)
              │
              └─ reply → gather context:
                          ├─ Google Calendar (upcoming events)
                          ├─ HubSpot (contact / company / deals)
                          ├─ Ashby ATS (candidate record)
                          ├─ Notion (knowledge-base)
                          └─ Gong (recent call activity)
                        → draft reply with Claude Opus
                        → save to Gmail Drafts + label EA/Processed
```

Drafts are **never sent automatically** — you review and send them yourself.

---

## Quick start

### 1 — Fork this repository

Click **Fork** so you have your own copy to configure.

### 2 — Set up Gmail API access

> **Google Cloud setup** (one-time):
> 1. Open [console.cloud.google.com](https://console.cloud.google.com)
> 2. Create a project → enable the **Gmail API** (and optionally the **Google Calendar API**)
> 3. Create **OAuth 2.0 credentials** (type: *Desktop application*)
> 4. Note the Client ID and Client Secret

```bash
# Install the one-time setup dependency
pip install google-auth-oauthlib

# Run the interactive OAuth helper
python3 scripts/setup_gmail_auth.py
```

The script opens a browser, asks you to grant Gmail access, and prints three
values you'll need in the next step:

```
GMAIL_CLIENT_ID=...
GMAIL_CLIENT_SECRET=...
GMAIL_REFRESH_TOKEN=...
```

> To also enable **Google Calendar** context, re-run the setup script after
> adding `calendar.readonly` to the OAuth scopes, then update `GMAIL_REFRESH_TOKEN`.

### 3 — Add GitHub Secrets

Go to your fork → **Settings → Secrets and variables → Actions → New repository secret**

**Required**

| Secret | Description |
|--------|-------------|
| `GMAIL_CLIENT_ID` | From step 2 |
| `GMAIL_CLIENT_SECRET` | From step 2 |
| `GMAIL_REFRESH_TOKEN` | From step 2 |
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com/settings/keys) |

**Optional integrations**

| Secret | Description |
|--------|-------------|
| `HUBSPOT_ACCESS_TOKEN` | HubSpot Private App token (CRM context) |
| `ASHBY_API_KEY` | Ashby API key (candidate / recruiting context) |
| `NOTION_API_KEY` | [notion.so/my-integrations](https://www.notion.so/my-integrations) |
| `NOTION_DATABASE_ID` | ID of your Notion knowledge-base database |
| `NOTION_PAGE_IDS` | Comma-separated Notion page IDs (alternative to database) |
| `GONG_BASE_URL` | Your Gong instance base URL (e.g. `https://us-XXXXX.api.gong.io`) |
| `GONG_ACCESS_KEY` | Gong API access key |
| `GONG_ACCESS_SECRET` | Gong API access secret |

**Optional variables** (Settings → Secrets and variables → Actions → **Variables**)

| Variable | Default | Description |
|----------|---------|-------------|
| `GOOGLE_CALENDAR_ENABLED` | `false` | Set to `true` to pull upcoming calendar events into drafts |

### 4 — Personalise your persona

Edit **`config/persona.yaml`** with your name, role, and writing preferences.
This file is committed to the repo — it contains no secrets.

```yaml
name: "Jane Smith"
role: "CEO"
company: "Acme Corp"
tone: "warm and direct"
notes: |
  - Keep replies short and direct.
  - Never commit to a meeting without checking my calendar first.
  - When declining, always offer an alternative.
```

Rules applied automatically:
- No sign-off or closing name (your Gmail signature handles that)
- No valedictions like "Best" or "Thanks"
- In French: greet with "hello", never "salut"

### 5 — Enable GitHub Actions

Go to **Actions → AI Email Assistant → Enable workflow**.

The assistant runs 4× per day (7 AM, 12 PM, 5 PM, 8 PM ET). You can also
trigger it manually via **Actions → AI Email Assistant → Run workflow**, where
you can override `max_emails` and enable `dry_run`.

---

## Configuration reference

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_EMAILS` | `10` | Max emails processed per run |
| `DRY_RUN` | `false` | Classify and draft in memory but do not touch Gmail |

---

## Optional integrations

### Google Calendar

Re-authorise the OAuth token with the `calendar.readonly` scope, then set the
`GOOGLE_CALENDAR_ENABLED` repository **variable** to `true`. The assistant will
include your upcoming events when drafting scheduling-related replies.

### HubSpot

1. In HubSpot go to **Settings → Integrations → Private Apps → Create a private app**
2. Grant scopes: `crm.objects.contacts.read`, `crm.objects.companies.read`, `crm.objects.deals.read`
3. Copy the access token → add as `HUBSPOT_ACCESS_TOKEN` secret

For each email sender the assistant fetches their contact record, associated
company, open deals, and recent CRM notes.

### Ashby ATS

Add your `ASHBY_API_KEY` secret. For emails from candidates or recruiters, the
assistant looks up the sender in Ashby and includes their application status and
stage as context.

### Notion

1. Create a Notion integration at [notion.so/my-integrations](https://www.notion.so/my-integrations)
2. Share your database or pages with the integration
3. Add `NOTION_API_KEY` and either `NOTION_DATABASE_ID` or `NOTION_PAGE_IDS` as secrets

### Gong

Add `GONG_BASE_URL`, `GONG_ACCESS_KEY`, and `GONG_ACCESS_SECRET` as secrets.
The assistant can include recent call activity for a contact when drafting
follow-up emails.

---

## Local development

```bash
# Clone your fork
git clone https://github.com/YOUR_USERNAME/YOUR_REPO
cd YOUR_REPO

# Install dependencies
pip install -r requirements.txt

# Dry run — classifies emails and prints drafts without touching Gmail
DRY_RUN=true \
GMAIL_CLIENT_ID=... \
GMAIL_CLIENT_SECRET=... \
GMAIL_REFRESH_TOKEN=... \
ANTHROPIC_API_KEY=... \
python3 src/main.py
```

---

## Project structure

```
.
├── .github/workflows/
│   └── email-assistant.yml   # GitHub Actions — 4× daily cron + manual trigger
├── config/
│   └── persona.yaml          # Your writing persona (commit this, no secrets)
├── scripts/
│   └── setup_gmail_auth.py   # One-time Gmail OAuth token generator
├── src/
│   ├── main.py               # Orchestrator
│   ├── ai_assistant.py       # Claude API — classify + draft
│   ├── gmail_client.py       # Gmail API — fetch threads, create drafts, manage labels
│   ├── calendar_context.py   # Google Calendar context
│   ├── hubspot_context.py    # HubSpot CRM context
│   ├── ashby_context.py      # Ashby ATS candidate context
│   ├── notion_context.py     # Notion knowledge-base context
│   └── (gong_context.py)     # Gong call context (uses Gong API secrets)
├── .env.example              # Template for local environment variables
├── .gitignore
└── requirements.txt
```

---

## Security notes

- **No credentials are stored in the repository.** All secrets live in GitHub
  Actions Secrets and are injected at runtime.
- Drafts are saved to Gmail and **never sent automatically**.
- The `EA/Processed` label prevents the same email from being processed twice.
- You can revoke access at any time: delete the GitHub Secrets and/or revoke
  the OAuth app at [myaccount.google.com/permissions](https://myaccount.google.com/permissions).

---

## Contributing

Pull requests are welcome. Please open an issue first to discuss significant changes.

## License

MIT
