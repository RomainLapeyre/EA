# Skill: process-emails

Trigger a dry-run of the EA email assistant and show the output.

## What this does

Runs `python src/main.py` with `DRY_RUN=true` so you can see exactly which
emails would be drafted, classified, or archived — without touching Gmail.

## Steps

1. Set environment variables from `.env` (if present).
2. Run the assistant in dry-run mode.
3. Print a summary of what happened.

## Prompt

```
Run the EA email assistant in dry-run mode using the environment variables in .env (or exported in the current shell). Show me:
- Which emails were classified as newsletters and would be archived
- Which emails would get a draft reply, and show the draft body
- Any errors

Command: DRY_RUN=true python src/main.py

After running, give me a short summary of the results.
```
