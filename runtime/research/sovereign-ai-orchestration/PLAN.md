# SovereignResearchAssistant — Execution Plan

## 1. Real research pass with sources
- Send `/research/assistant/run` with `sources` array pointing to local evidence files
- Verify artifacts written to `runtime/research/<slug>/`

## 2. Telegram notification on completion
- Patch `/research/assistant/runs/{slug}/complete` to call `telegram_direct_send.py`
- Verify Telegram delivery after a POST

## 3. Report hardening
- Add markdown report export endpoint
- Add claim confidence scoring in `research_assistant.py`
- Add artifact index response in `/research/assistant/latest`

## 4. Hook 4h verifier cron
- Update `powerup_verify.py` to hit `/research/assistant/preflight`, `/safety/status`, `/evolution/scan`
- Keep no-agent script behavior for cron compatibility

## 5. Phase 9: Autonomous research loop
- Add cron job: every 240m run `/research/assistant/run` with a rotating topic list
- Persist results to Telegram via direct send
