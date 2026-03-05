Update Romain's writing persona based on this instruction: $ARGUMENTS

1. Read `config/persona.yaml` and `src/ai_assistant.py` (the system prompt template).
2. Apply the requested change to the right place:
   - Tone / style rules → `config/persona.yaml` under `notes:`
   - Structural rules (sign-off, subject line, preamble) → the `_SYSTEM_PROMPT_TEMPLATE` in `src/ai_assistant.py`
3. Show me the diff before committing.
4. Commit and push.
