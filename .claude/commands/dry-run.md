Run the EA email assistant in dry-run mode and show me what it would do.

1. Load env vars from `.env` if the file exists, otherwise use whatever is already exported in the shell.
2. Run: `DRY_RUN=true python src/main.py`
3. After it finishes, give me a short structured summary:
   - How many emails were classified as newsletters / archived
   - How many draft replies were generated
   - Show each draft body in full
   - List any errors
