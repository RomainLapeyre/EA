# AI Email Assistant

An open-source executive assistant that runs entirely on **GitHub Actions**.
It reads your unread Gmail, drafts replies using **Claude (Anthropic)**, and
saves them back to your **Gmail Drafts** folder — so you always have final
approval before anything is sent.

Optional integrations enrich every draft with CRM data from **HubSpot** and
knowledge-base context from **Notion**.

---

## How it works

```
Every hour (GitHub Actions cron)
  └─ Fetch unread Gmail messages
        └─ For each email:
              ├─ Fetch thread history
              ├─ Look up sender in HubSpot (optional)
              ├─ Query Notion knowledge base (optional)
              └─ Ask Claude to draft a reply
                    └─ Save draft to Gmail Drafts
                          └─ Label email "EA/Processed"
```

Drafts are **never sent automatically** — you review and send them yourself.

---

## Quick start

### 1 — Fork this repository

Click **Fork** so you have your own copy to configure.

### 2 — Set up Gmail API access

```bash
# Install the one-time setup dependency
pip install google-auth-oauthlib

# Run the interactive OAuth helper
python scripts/setup_gmail_auth.py
```

The script opens a browser, asks you to grant Gmail access, and prints three
values you'll need in the next step:

```
GMAIL_CLIENT_ID=...
GMAIL_CLIENT_SECRET=...
GMAIL_REFRESH_TOKEN=...
```

> **Google Cloud setup** (one-time):
> 1. Open [console.cloud.google.com](https://console.cloud.google.com)
> 2. Create a project → enable the **Gmail API**
> 3. Create **OAuth 2.0 credentials** (type: *Desktop application*)
> 4. Note the Client ID and Client Secret

### 3 — Add GitHub Secrets

Go to your fork → **Settings → Secrets and variables → Actions → New repository secret**

| Secret | Required | Description |
|--------|----------|-------------|
| `GMAIL_CLIENT_ID` | ✅ | From step 2 |
| `GMAIL_CLIENT_SECRET` | ✅ | From step 2 |
| `GMAIL_REFRESH_TOKEN` | ✅ | From step 2 |
| `ANTHROPIC_API_KEY` | ✅ | [console.anthropic.com](https://console.anthropic.com/settings/keys) |
| `NOTION_API_KEY` | ☐ | [notion.so/my-integrations](https://www.notion.so/my-integrations) |
| `NOTION_DATABASE_ID` | ☐ | ID of your Notion knowledge-base database |
| `NOTION_PAGE_IDS` | ☐ | Comma-separated Notion page IDs (alternative to database) |
| `HUBSPOT_ACCESS_TOKEN` | ☐ | HubSpot Private App token (see below) |

### 4 — Personalise your persona

Edit **`config/persona.yaml`** with your name, role, and writing preferences.
This file is committed to the repo — it contains no secrets.

```yaml
name: "Jane Smith"
role: "CEO"
company: "Acme Corp"
tone: "warm and direct"
sign_off: "Best,\n{name}"
notes: |
  - I prefer bullet points over long paragraphs.
  - Never commit to a meeting without checking my calendar first.
  - When declining, always offer an alternative.
```

### 5 — Enable GitHub Actions

Go to **Actions → AI Email Assistant → Enable workflow**.

The assistant will now run every hour. You can also trigger it manually via
**Actions → AI Email Assistant → Run workflow**.

---

## Configuration reference

All settings can be controlled via environment variables (set as GitHub Secrets
or in a local `.env` file). See **`.env.example`** for the full list.

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_EMAILS` | `10` | Max emails processed per run |
| `DRY_RUN` | `false` | Analyse but do not create drafts |
| `EMAIL_TONE` | `professional and concise` | Fallback tone if persona.yaml is blank |

---

## Notion integration

1. Create a Notion integration at [notion.so/my-integrations](https://www.notion.so/my-integrations)
2. Share your database or pages with the integration
3. Add `NOTION_API_KEY` and either `NOTION_DATABASE_ID` or `NOTION_PAGE_IDS` as Secrets

The assistant queries the database for relevant entries and includes them as
context when drafting replies.

---

## HubSpot integration

1. In HubSpot go to **Settings → Integrations → Private Apps → Create a private app**
2. Grant scopes: `crm.objects.contacts.read`, `crm.objects.companies.read`, `crm.objects.deals.read`
3. Copy the access token and add it as the `HUBSPOT_ACCESS_TOKEN` secret

For each email sender, the assistant will fetch their contact record, associated
company, open deals, and recent CRM notes.

---

## Local development

```bash
# Clone your fork
git clone https://github.com/YOUR_USERNAME/YOUR_REPO
cd YOUR_REPO

# Install dependencies
pip install -r requirements.txt

# Copy and fill in your credentials
cp .env.example .env
# ... edit .env ...

# Run in dry-run mode (no drafts created)
DRY_RUN=true python src/main.py

# Run normally
python src/main.py
```

---

## Project structure

```
.
├── .github/workflows/
│   └── email-assistant.yml   # GitHub Actions — hourly cron + manual trigger
├── config/
│   └── persona.yaml          # Your persona (edit this, commit it, no secrets here)
├── scripts/
│   └── setup_gmail_auth.py   # One-time Gmail OAuth token generator
├── src/
│   ├── main.py               # Orchestrator
│   ├── gmail_client.py       # Gmail API — fetch emails, create drafts, manage labels
│   ├── notion_context.py     # Notion API — knowledge-base context
│   ├── hubspot_context.py    # HubSpot API — CRM contact/deal context
│   └── ai_assistant.py       # Claude integration — prompt + draft generation
├── .env.example              # Template for environment variables (no secrets)
├── .gitignore
└── requirements.txt
```

---

## Security notes

- **No credentials are stored in the repository.** All secrets live in GitHub
  Actions Secrets and are injected at runtime.
- Drafts are saved to Gmail and **never sent automatically**.
- The `EA/Processed` Gmail label prevents the same email from being processed
  twice.
- You can always revoke access: delete the GitHub Secrets and/or revoke the
  OAuth app in your [Google Account permissions](https://myaccount.google.com/permissions).

---

## Contributing

Pull requests are welcome. Please open an issue first to discuss significant
changes.

## License

MIT
