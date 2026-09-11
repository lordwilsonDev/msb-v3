# Governed-Loop Alerting — Design Spec

**Date:** 2026-09-11
**Status:** Approved
**Blueprint tie-in:** `docs/blueprints/2026-09-09-production-hardening.md` §3.6 (Strengthen Observability of the Governed Loop) — the one piece of step 6 that was genuinely missing (metrics themselves already exist at `/metrics/prometheus`; nothing watches them).

---

## Purpose

MSB v3 already exposes rich Prometheus metrics (Triumvirate subsystem counters, `msb_v3_actiongate_decisions_total`, readiness/latency gauges) and a `/system/health` endpoint with degradation detection. Nothing currently watches either — an ActionGate BLOCK-rate spike, a killswitch trip, or prolonged local-inference degradation is only visible if Wilson happens to be looking at the dashboard at that moment. This closes that gap with the smallest thing that actually works: a periodic in-process check that pushes a Telegram message (via the existing Hermes agent's `send` CLI) when something crosses a threshold, and again when it recovers.

## Non-goals (v1)

- No dashboard (Cockpit already exists for that).
- No Alertmanager / Grafana — that's real new infrastructure with its own supervision burden, which cuts against blueprint step 4 (reduce operational surface area). Revisit only if the lightweight approach proves insufficient.
- No detection of "the whole server process died" — already handled by launchd's `KeepAlive` on `com.lordwilson.msb-v3` (auto-restart on crash). A different failure class; not in scope here.

## Architecture

One new cron action, `alert_check`, registered in MSB v3's **existing** in-process cron scheduler (`src/msb_v3/cron/`). No new process, no new launchd unit — it inherits the scheduler's existing timeout, retries, kill-switch gating, run history, and audit-chain recording for free, exactly like every other cron action (`action_health_check` is the closest existing sibling to pattern against).

## Rules (v1 — deliberately minimal, not the whole blueprint wishlist)

Edge-triggered: a message fires on a state **change** (healthy→bad or bad→healthy), never repeatedly while a condition persists. State is tracked in a small local file so the check survives process restarts without re-alerting on unrelated data.

| # | Rule | Trigger condition | Recovery notice |
|---|------|--------------------|------------------|
| 1 | Killswitch armed | `governance` reports killswitch transitions disarmed → armed | Yes, on armed → disarmed |
| 2 | ActionGate BLOCK/FAIL spike | More than 5 `failed` + `denied` verdicts (from `msb_v3_actiongate_decisions_total`) accumulate within a 15-minute window that **resets** every 15 minutes (not a sliding buffer — simplest correct implementation, no per-event history to store) | Yes, once a completed window ends under threshold |
| 3 | System degraded | `/system/health`'s `overall` field reports `degraded` or `FAILED` on **2 consecutive polls** (10 minutes) — filters a single transient blip | Yes, once `overall` returns to `healthy` |

Poll interval: **5 minutes** (matches the existing wake-agent cron job's cadence for consistency; cheap enough that a tighter interval buys little).

## Data flow

1. Cron scheduler invokes `action_alert_check(params)` every 5 minutes.
2. The action makes two loopback HTTP calls (matches the existing `http_call` action's loopback-only allowlist convention):
   - `GET http://127.0.0.1:8766/system/health` → `overall` status, and (for killswitch) whatever governance state is exposed there or via a second lightweight governance-status read.
   - `GET http://127.0.0.1:8766/metrics/prometheus` → parse `msb_v3_actiongate_decisions_total{verdict="failed"}` and `{verdict="denied"}`.
3. Load prior state from `data/cron/alert_watch_state.json`:
   ```json
   {
     "actiongate_failed_denied_total_at_last_poll": 0,
     "actiongate_window_start": "2026-09-11T00:00:00Z",
     "actiongate_window_count": 0,
     "consecutive_degraded": 0,
     "killswitch_armed": false,
     "alerts_active": {"killswitch": false, "actiongate_rate": false, "system_degraded": false}
   }
   ```
4. Compute each rule's current state; diff against `alerts_active`.
5. For any rule whose active/inactive state just flipped, call:
   ```bash
   hermes send -t telegram "<message>"
   ```
   as a bounded subprocess (timeout, no shell=True, matching the codebase's existing subprocess-safety convention).
6. Persist updated state back to the JSON file.
7. Return `{"ok": True/False, "summary": ..., "detail": {...}}` per the existing `ActionFn` contract — a `hermes send` failure or an unreachable local endpoint returns `ok: False` (visible in cron run history / audit chain) but never raises, never crashes the scheduler.

## Message format

Plain text, short, actionable:

```
🚨 MSB v3 ALERT: killswitch ARMED — execution halted. Investigate immediately.
🚨 MSB v3 ALERT: ActionGate spike — 7 denied/failed in 15min (threshold 5). Check /cockpit.
⚠️ MSB v3: system health degraded 10+ min (ollama: error: ...). Check /system/health.
✅ MSB v3 RECOVERED: killswitch disarmed — execution resumed.
✅ MSB v3 RECOVERED: ActionGate rate back to normal.
✅ MSB v3 RECOVERED: system health back to healthy.
```

## Registration

- Add `"alert_check": action_alert_check` to the existing `ACTIONS` dict in `src/msb_v3/cron/actions.py` (same dict `"health_check"` is already in).
- Add `ensure_alert_check_job()` in `src/msb_v3/wake/runner.py` (or a sibling module — whichever the implementation plan finds cleaner), following the exact shape of `ensure_wake_job()`: idempotent, seeds a cron job named `alert-check` with schedule from a new `settings.alert_check_schedule` (default every 5 minutes), wired into app lifespan the same place `ensure_wake_job()` is called.

## Testing

Unit tests (mocking the two HTTP polls and the `hermes send` subprocess call — no real network, no real Telegram send):

1. Baseline: healthy system, low ActionGate volume → no alert sent, state file unchanged in substance.
2. Each of the 3 rules individually crossing its threshold → exactly one `hermes send` call with the right message.
3. Rule stays breached across multiple polls → only **one** alert sent (edge-trigger suppression), not one per poll.
4. Rule recovers → exactly one recovery message sent.
5. `hermes send` itself fails (non-zero exit) → action returns `{"ok": False, ...}`, does not raise.
6. `/system/health` or `/metrics/prometheus` unreachable → action returns `{"ok": False, ...}`, does not raise, does not corrupt the state file.

## Open items for the implementation plan (not blocking this spec)

- Exact governance/killswitch read path (HTTP endpoint vs. in-process import) — implementation plan should check what's cheapest and most consistent with `action_health_check`'s existing pattern.
- Exact location of `ensure_alert_check_job()` (wake/runner.py vs. a new small module) — a judgment call for whoever writes the plan, guided by keeping cron-seeding logic in one place if reasonable.
