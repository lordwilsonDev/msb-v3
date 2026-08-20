# Wake Loop — the 5-minute resident agent

The resident agent: msb-v3 wakes itself every 5 minutes and processes
messages left for it from **any** session, then leaves replies in an outbox
you can read from anywhere else. You can be mid-task in one session, drop a
message in another, and the resident agent answers on the next wake.

## How it works

```
any session ──POST /wake──▶ wake inbox ──wake-agent cron job──▶ wake outbox ──GET /wake/outbox──▶ any session
                           (SQLite)     (*/5 * * * *, governed)   (SQLite)
```

- **The cadence is a cron job**, not a new daemon: the existing cron
  scheduler (the heartbeat) runs `wake_agent` on `MSB_WAKE_SCHEDULE`
  (default `*/5 * * * *`). The job is seeded automatically on server start
  (`app.py` lifespan) — nothing to set up.
- **The turn is a governed action**: it runs under the scheduler's kill
  switch, retries, timeout, run history, and evidence receipt — the same
  discipline as backups and health checks. Bounded at
  `MSB_WAKE_MAX_PER_RUN` (default 5) messages per cycle.
- **The brain is DeepSeek** (the $10 key). No `DEEPSEEK_API_KEY`? The turn
  fails loudly and the message stays `failed` with the reason — never a
  silent no-op.
- **Automations**: if a wake message asks the agent to build an automation,
  the runner hands the plan to the automation brain (see
  [automation-brain.md](automation-brain.md)), which **dry-runs by default** —
  the reply tells you the plan and what approval would create.
- **One clock, three legs**: every wake pass runs the inbox **and** the
  dispatcher (due *living* automations — Stage 1) **and** the
  self-maintenance audit (Stage 4). The heartbeat is the single nervous
  system: messages, automations, and self-healing all tick on it.
- **The webhook sense**: external platforms forward payloads to
  `POST /hook/<automation_id>` (no auth — the edge is the optional
  `MSB_AUTOMATION_HOOK_SECRET` + bounded payloads); each payload lands in
  the inbox tagged with the automation id, and the next wake decides what
  it means (Stage 3).

## Usage

```bash
# From any session — leave a message for the resident agent
curl -X POST http://127.0.0.1:8766/wake \
  -H "Authorization: Bearer $MSB_OPERATOR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"text": "check the disk headroom and remind me Friday", "from": "freebuff"}'

# Read the resident agent's replies (newest first)
curl http://127.0.0.1:8766/wake/outbox -H "Authorization: Bearer $MSB_OPERATOR_TOKEN"

# Inbox depth + loop status
curl http://127.0.0.1:8766/wake/status -H "Authorization: Bearer $MSB_OPERATOR_TOKEN"

# The loop is just a cron job — run it now, watch history, or change cadence
python -m msb_v3.cron run wake-agent
python -m msb_v3.cron history wake-agent
python -m msb_v3.cron add --name "wake-agent" --schedule "*/10 * * * *" \
  --action wake_agent
```

## Config

| Env | Default | Meaning |
|---|---|---|
| `MSB_WAKE_ENABLED` | `1` | seed the wake-agent job on server start |
| `MSB_WAKE_SCHEDULE` | `*/5 * * * *` | the resident cadence |
| `MSB_WAKE_MAX_PER_RUN` | `5` | messages processed per cycle (bounded) |
| `MSB_WAKE_DB_PATH` | (derived) | inbox/outbox store (data/runtime/wake.db) |

## Guarantees

- **Bounded**: one bad turn never aborts the cycle; the failed message keeps
  its error visible in the inbox.
- **No silent drops**: pending → done | failed. A failed message is never
  silently retried; re-post it if it matters.
- **Fail-closed**: `MSB_OPERATOR_TOKEN` unset = the /wake surface is closed
  (503), same as /cron.
