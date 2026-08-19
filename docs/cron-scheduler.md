# Cron Scheduler — the heartbeat

Scheduled, governed jobs for msb-v3. Durable job definitions + run history in
SQLite, a 5-field cron parser, six built-in actions, an operator-gated REST
API, a CLI, and an in-process async loop that wakes on a tick and runs
whatever is due. Every execution is governed exactly like the rest of the
runtime: kill-switch fail-closed, retries, timeout, overlap guard, an evidence
receipt on the audit stream, and a record on the UAC AuditChain.

## What it is for

The system is reactive by default — it waits for a request. Cron makes it
proactive: self-auditing (`audit_chain_verify`), self-maintenance
(`backup_spine`, `log_rotation`, `metric_export`), and health verification
(`health_check`) run on a schedule, unattended, with the same evidence trail
as a governed run.

## Quick start

```bash
# List jobs
python -m msb_v3.cron list

# Add a daily backup at 02:00, keeping 14 backups
python -m msb_v3.cron add --name "Daily Backup" --schedule "0 2 * * *" \
    --action backup_spine --param keep=14

# Run it now (governed) — safe to call from launchd/cron
python -m msb_v3.cron run daily-backup

# Recent run history
python -m msb_v3.cron history daily-backup

# Remove
python -m msb_v3.cron remove daily-backup
```

The REST surface is `/cron` (operator-gated, `MSB_OPERATOR_TOKEN`):

| Endpoint | Purpose |
|---|---|
| `GET /cron/jobs` | list jobs (with derived `next_run`) |
| `POST /cron/jobs` | create a job |
| `GET /cron/jobs/{id}` | one job |
| `PATCH /cron/jobs/{id}` | update (schedule / enabled / action / governance) |
| `DELETE /cron/jobs/{id}` | remove a job |
| `POST /cron/jobs/{id}/run` | run now (governed) |
| `GET /cron/jobs/{id}/history` | recent runs |
| `GET /cron/status` | scheduler status (enabled, tick, in-flight runs) |

## Schedules

Standard 5-field cron expressions (numeric only — no `JAN`/`MON` names):

```
* * * * *     minute hour day-of-month month day-of-week (0-7, 0/7 = Sunday)
*/15 * * * *  every 15 minutes
0 2 * * *     daily at 02:00
0 */6 * * *   every 6 hours
0 0 1 * 1     first of the month OR any Monday (Vixie semantics)
```

Both day-of-month and day-of-week restricted → the expression fires when
*either* matches (Vixie cron behavior). The parser is in
`src/msb_v3/cron/parser.py` and is unit-tested; a malformed schedule is
rejected at the boundary, so a bad job can never be stored.

## The six built-in actions

| Action | What it does | Key params |
|---|---|---|
| `health_check` | SQLite readable, audit chain readable, kill-switch state readable (fail-closed) | — |
| `audit_chain_verify` | Hash-chain internal verify + external anchor verify; a broken chain is a FAILED run | — |
| `backup_spine` | Snapshot only-copy data (SQLite online-backup + file copies) into the backup root, then prune | `keep` (14), `destination` |
| `metric_export` | Write the Prometheus registry (text + JSON) to `runtime/exports/` | `destination`, `json` |
| `log_rotation` | Rotate `audit.jsonl` by size; gzip-snapshot stale `*.log`; prune old archives | `max_bytes`, `max_age_days`, `keep_days`, `log_dir` |
| `http_call` | Call an HTTP endpoint — **localhost-only by default** | `url`, `method`, `json`, `headers`, `timeout_s` |

### http_call safety

Fail-closed: the URL's host must be on the `MSB_CRON_HTTP_HOSTS` allowlist
(default `127.0.0.1,localhost,::1`). Anything else is refused and the run is
recorded as FAILED. Widen the allowlist deliberately (e.g. an internal
service) by adding hosts — never by removing the loopback default.

### log_rotation safety

`audit.jsonl` is reopened per append in-process, so a rename-based rotation is
lossless. Other `logs/*.log` files may be held open by external processes
(launchd); those are never deleted or truncated — they are archived as gzip
*snapshots* and the live file is left alone. Truncating externally-held files
is deliberately deferred.

## Governance model

Every run — scheduled or manual — passes the same gates:

1. **Kill switch** — fail-closed. An armed switch blocks the run and records
   it as `BLOCKED` (a denial is evidence, not silence).
2. **Overlap guard** — a job with an in-flight run is never started twice
   (recorded `SKIPPED`).
3. **`requires_approval`** — such jobs never auto-run on schedule (recorded
   `SKIPPED`, parked for the operator). Only an operator-gated manual run
   (`POST /cron/jobs/{id}/run` or `python -m msb_v3.cron run`) executes them
   — the operator token *is* the approval.
4. **Retries + timeout** — `max_retries` attempts, each bounded by `timeout_s`
   (async timeout; a runaway action is killed and recorded FAILED).
5. **Evidence** — one receipt per run on `logs/audit.jsonl`
   (`event: "cron.run"`, `basis: "rerun"` — the action actually executed
   against ground truth) and a `cron.<status>` record on the UAC AuditChain
   (best-effort mirror; a chain outage never breaks the run). The run row
   lands in the `cron_runs` projection.

On scheduler start, in-flight `RUNNING` rows from a previous process are
marked `INTERRUPTED` — never silently resumed, never silently dropped.

## Running the loop

The FastAPI lifespan starts the loop when `MSB_CRON_ENABLED=1` (default).
It wakes every `MSB_CRON_TICK_S` seconds (default 15) and runs due jobs.
Tests disable it via the suite-wide conftest fixture.

Because a manual `run` is the exact same governed path, launchd (or a system
cron line) can drive jobs without running a daemon:

```
0 2 * * * cd $MSB_REPO && python -m msb_v3.cron run daily-backup
```

## Storage

`data/runtime/cron.db` (beside `runtime.db` / `tasks.db`) — derived
projection, same philosophy as the runtime store: the audit chain is the
record, the store answers "what did the scheduler do". History is pruned to
`MSB_CRON_HISTORY_KEEP` rows per job (default 100). `MSB_CRON_DB_PATH`
overrides the location.

## Env

| Var | Default | Meaning |
|---|---|---|
| `MSB_CRON_ENABLED` | `1` | start the in-process loop on boot |
| `MSB_CRON_TICK_S` | `15` | loop wake cadence |
| `MSB_CRON_DB_PATH` | `data/runtime/cron.db` | job + run store |
| `MSB_CRON_HISTORY_KEEP` | `100` | run rows retained per job |
| `MSB_CRON_HTTP_HOSTS` | `127.0.0.1,localhost,::1` | `http_call` host allowlist |
