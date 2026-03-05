Add a new external context source to the EA email assistant.

Source to add: $ARGUMENTS

Follow these steps exactly:

1. Read `src/hubspot_context.py` to understand the pattern.
2. Create `src/<name>_context.py` with a `<Name>ContextClient` class:
   - `__init__(self, api_key/credentials)` — store the authenticated session
   - `get_<entity>_context(self, email: str) -> str` — search by email, return formatted text or `""` if nothing found, never raise
3. In `src/main.py`:
   - Import the new client
   - Initialise it (optional, gated by an env var like `<NAME>_API_KEY`)
   - Call `get_<entity>_context(sender)` and store the result
   - Pass it as a new keyword arg to `ai.generate_draft_reply()`
4. In `src/ai_assistant.py`:
   - Add the new keyword arg to `generate_draft_reply()` and `_build_user_message()`
   - Append it to `parts` in `_build_user_message()` if non-empty
5. In `.github/workflows/email-assistant.yml`, add the env var under the optional secrets section.
6. Commit with a clear message and push to the current branch.
