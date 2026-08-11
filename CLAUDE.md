# CLAUDE.md

## Agent skills

### Issue tracker

GitHub Issues. See `docs/agents/issue-tracker.md`.

### Triage labels

`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context layout: `CONTEXT.md` + `docs/adr/`. See `docs/agents/domain.md`.

## Stack

FastAPI + SQLite + Ollama + Prometheus. No `.venv`; Python is at `/opt/homebrew/Caskroom/miniforge/base/bin/python`.

## Scripts

- `make test` / `make server` / `make smoke` / `make server-start|stop|status` (launchd/standby control)
- `make hygiene` (full battery + auto-sweep of test Qdrant collections)
- `make qdrant-start` / `make qdrant-stop` / `make qdrant-status` / `make qdrant` (status alias)
- `make qdrant-sweep` (delete test-named collections; `ARGS=--dry-run` to preview)
- `make portability` — prove the full suite passes from a foreign checkout
  path (stages a temp copy, scans for machine literals, runs pytest there;
  the pre-push portability gate, wired into the harness-gate CI job)
- `scripts/start.sh` / `scripts/stop.sh` (`start` / `stop` / `status`) — launchd-aware server control
- `scripts/start-qdrant.sh` (`start` / `stop` / `status`) — Qdrant control

## Server supervision

msb-v3 on :8766 is supervised by LaunchAgent `com.lordwilson.msb-v3` (source:
`scripts/launchd/com.lordwilson.msb-v3.plist`; installed copy
`~/Library/LaunchAgents/`, keep in sync): launchd runs `scripts/run.sh`
(the app supervisor) with `KeepAlive`, so the server starts at login and
crashes auto-restart. `scripts/start.sh` detects the agent and maps
start/stop/status to `launchctl bootstrap`/`bootout`/`print`, falling back to
nohup+pidfile in non-GUI sessions (SSH); stop unloads the agent, start
reloads it (symmetric). Verify with
`launchctl print gui/$(id -u)/com.lordwilson.msb-v3`. Standby pidfile
`.artifacts/msb-v3.pid` tracks the run.sh supervisor.

## Ports

- msb-v3: `:8766`
- Ollama: `:11434`
- Qdrant: `:6333`

## Qdrant

Vector store for the RAG/vault index (`tenant_wilson-vault`, ~5.4k chunks).

- **Recovery one-liner if `:6333` is down:** `scripts/start-qdrant.sh start`
- **Must run from the repo root** — this qdrant build has no `--storage-path`
  flag; it resolves storage as `./storage` relative to its cwd (the tenant
  collections live at `$REPO/storage`). Launching from elsewhere silently
  creates fresh empty storage — the 2026-08 "data loss" trap.
- Supervised by LaunchAgent `com.lordwilson.qdrant` (source:
  `scripts/launchd/com.lordwilson.qdrant.plist`): launchd runs qdrant
  directly with `WorkingDirectory=$REPO` and `KeepAlive`, so crashes
  auto-restart. start-qdrant.sh detects the agent and maps start/stop/status
  to `launchctl bootstrap`/`bootout`; verify with
  `launchctl print gui/$(id -u)/com.lordwilson.qdrant`.
- pidfile `.artifacts/qdrant.pid`, log `logs/qdrant.log`; index freshness via
  `~/bin/vault-check.py --fresh` (reindex: `--reindex`, ~10 min under tmux).
- Cleanup routine: the live integration test and battery runners delete their
  own throwaway collections in a `finally` block; `make qdrant-sweep` remains
  the safety net (or just use `make hygiene`, which sweeps automatically).

## Git

Dual-push: `origin` and `sovereign_intelligence_core` both point to `https://github.com/lordwilsonDev/msb-v3.git`.

## MCP

`msb-mcp-server` at `/Users/lordwilson/msb-mcp-server` provides `msb-v3.chat/memory_*`, `status`, `metrics_*` tools.

## Defaults

- Primary LLM: DeepSeek V4-Flash.
- Vault: `~/Documents/Vault` (git-tracked; override with `MSB_VAULT_PATH`).
  Repo root resolves from the package/script location; override with
  `MSB_HOME` (Python) or `MSB_REPO` (shell scripts).
- Obsidian-first data; skills automate on top.
- `runtime/research/` is regenerated on every run and never versioned
  (gitignored wholesale); keep hand-written research notes in `docs/`.
