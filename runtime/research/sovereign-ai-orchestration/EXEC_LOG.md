# Long-Horizon Harness — Execution Log

## 2026-07-30 — Phase 1-2 Complete
- Added `SovereignResearchAssistant` in `src/msb_v3/harnesses/research_assistant.py`.
- Routers: `src/msb_v3/api/research.py`, `src/msb_v3/api/safety.py`, `src/msb_v3/api/evolution.py`.
- Mounted in `src/msb_v3/api/app.py`:
  - `/research/*`
  - `/safety/*`
  - `/evolution/*`
- Model switched to `deepseek-r1:1.5b` on `msb-v3` port `8766`.
- Verified:
  - `/research/assistant/run` → 200 with local CoT output
  - `/safety/{status,evaluate,health,systems}` → 200
  - `/evolution/{scan,memory/latest,memory/summary}` → 200
- Broke stale process/installed-copy assumption; verified by factory-reloading uvicorn with explicit `PYTHONPATH=/Users/lordwilson/msb-v3/src`.

## Next
- Phase 2: keep local evidence grounding producing evidence + provenance artifacts.
- Phase 3-8: safety hardening, continuity, mesh, tests, completion appendices.
