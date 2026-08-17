# Governed-Loop Console — Live Verification

Screenshots captured from the running stack (Ollama `qwen3:8b` +
`nomic-embed-text`, app on `:8766`, v0.3.0) on 2026-08-17 at commit
`dbca2e3` (the commit that introduced `/console`), via ego-browser
(Chromium). These verify the console end-to-end in a real browser — the
same three views a human operator sees.

## What each screenshot shows

| File | View | Verified against |
|---|---|---|
| `01-token-gate.png` | The `/console` landing page: token field ("enter once — kept in sessionStorage"), request textarea, approve-writes select (default `false` = read-only), tenant, output dir, Run + Refresh buttons, empty recent-runs table | page title "MSB v3 — Governed Loop Console"; no token embedded in HTML |
| `02-run-verdict.png` | Result of a live governed run: verdict **PASS**, run_id `dbb-20260817T204225-99501`, deterministic hash `7aec748770e14f4f`, intent JSON, and the event-sourced replay table (TASK_CREATED → INTENT_INTERPRETED → PLAN_CREATED → AGENT_STARTED → TOOL_REQUESTED/EXECUTED ×2 → VERIFICATION_STARTED → VERIFICATION_PASSED → EVIDENCE_RECORDED → TASK_COMPLETED, audit seqs 10043–10058, derived state COMPLETED, consistent: true) | `GET /agent/tasks/{id}/replay` returned the same reconstruction |
| `03-replay-denied.png` | Replay of a **DENIED** run (`dbb-20260817T202704-12186`) via the per-row "replay" button — derived state DENIED, consistent: true, timeline TASK_CREATED → TASK_DENIED, empty decision trail | `GET /agent/tasks/dbb-20260817T202704-12186/replay` returned the same |

## Security model exercised

- Token entered once, held in `sessionStorage`, sent only as the
  `Authorization: Bearer` header to the same-origin gated API — the
  server-side `require_operator` gate is the authority (see
  `src/msb_v3/api/console.py`).
- The console adds no mutating route; it calls only the three documented
  gated endpoints (`POST /agent/handle`, `GET /agent/tasks`,
  `GET /agent/tasks/{id}/replay`).

## Re-running

```bash
bash scripts/start.sh start          # ensure the app is on current code
# open http://localhost:8766/console in a browser, paste MSB_OPERATOR_TOKEN,
# run a read-only request, click replay on a past run.
```
