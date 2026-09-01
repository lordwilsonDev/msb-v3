# CLAUDE.md

> Deep reference (CI runbooks, governance internals, flywheel internals, tag-ruleset
> emergency path) lives in `CLAUDE.archive.md`. This file is the operational surface.

## Agent skills

- **Issue tracker:** GitHub Issues. See `docs/agents/issue-tracker.md`.
- **Triage labels:** `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.
- **Domain docs:** single-context layout — `CONTEXT.md` + `docs/adr/`. See `docs/agents/domain.md`.

## Stack

FastAPI + SQLite + Ollama + Prometheus. No `.venv`; Python is at `/opt/homebrew/Caskroom/miniforge/base/bin/python`.

## Scripts

- `make test` / `make server` / `make smoke` / `make server-start|stop|status` (launchd/standby control)
- `make hygiene` (full battery + auto-sweep of test Qdrant collections)
- `make qdrant-start` / `make qdrant-stop` / `make qdrant-status` / `make qdrant` (status alias)
- `make qdrant-sweep` (delete test-named collections; `ARGS=--dry-run` to preview)
- `make portability` — prove the full suite passes from a foreign checkout path
  (stages a temp copy, scans for machine literals, runs pytest there; the
  pre-push portability gate, wired into the harness-gate CI job)
- `make hooks-install` / `make hooks-uninstall` — install/remove the pre-push
  hook that runs the portability gate before every push
- `make governance-status|arm|disarm|approvals|approve|reject|config` — the brakes;
  `config` prints the guard/brake/approval/flywheel settings (`--json` for verbatim)
- `make provision-models` — idempotent `ollama pull` of qwen3:8b + nomic-embed-text
- `make setup` — idempotent host rebuild from a fresh clone (deps, launchd agents, qdrant, models, /health smoke)
- `scripts/start.sh` / `scripts/stop.sh` (`start` / `stop` / `status`) — launchd-aware server control
- `scripts/start-qdrant.sh` (`start` / `stop` / `status`) — Qdrant control

## Server supervision

msb-v3 on :8766 is supervised by LaunchAgent `com.lordwilson.msb-v3` (source
`scripts/launchd/com.lordwilson.msb-v3.plist`; keep the installed copy in
`~/Library/LaunchAgents/` in sync). launchd runs `scripts/run.sh` with `KeepAlive`
(starts at login, crashes auto-restart). `scripts/start.sh` maps start/stop/status
to `launchctl bootstrap`/`bootout`/`print`, falling back to nohup+pidfile in
non-GUI sessions. Verify: `launchctl print gui/$(id -u)/com.lordwilson.msb-v3`.
Standby pidfile `.artifacts/msb-v3.pid`.

## GitHub Actions CI

Three gates run on push to main:
- `msb-v3 CI` (hosted) — tests 3.11/3.12, lint (ruff + mypy), security (pip-audit), docker build, claims verify
- `factory-gate` (hosted) — full suite + coverage floor + hygiene + auth + E2E
- `harness-gate` (**self-hosted** `msb-v3-mac-arm64`, `~/actions-runner`) — browser + video-harness evidence gate

Codecov token rotation, self-hosted runner registration, and the harness-evidence
freshener LaunchAgent: see `CLAUDE.archive.md` → "CI internals".

## Ports

- msb-v3: `:8766` · Ollama: `:11434` · Qdrant: `:6333`

## Qdrant

Vector store for the RAG/vault index (`tenant_wilson-vault`, ~5.4k chunks).

- **Recovery one-liner if `:6333` is down:** `scripts/start-qdrant.sh start`
- **Must run from the repo root** — this qdrant build has no `--storage-path` flag;
  it resolves storage as `./storage` relative to cwd (tenant collections live at
  `$REPO/storage`). Launching from elsewhere silently creates fresh empty
  storage — the 2026-08 "data loss" trap.
- Supervised by LaunchAgent `com.lordwilson.qdrant` (`WorkingDirectory=$REPO`,
  `KeepAlive`). Index freshness: `~/bin/vault-check.py --fresh` (reindex `--reindex`, ~10 min).
- `make qdrant-sweep` / `make hygiene` clean stray test collections.

## Governance brakes (Phase 0B)

Autonomy brakes the flywheel runs behind. Package `msb_v3/governance/`, HTTP
`/governance/*`, CLI `python -m msb_v3.governance` (or `make governance-*`).
State in SQLite (`data/governance/governance.db`), all decisions audited to the
UAC `AuditChain`. Fail-closed: unreadable state ⇒ halted/denied. Brakes:
Ouroboros governor, budget caps, approval queue, kill switch — details in
`CLAUDE.archive.md` → "Governance internals". `POST /governance/check` is the
drill endpoint (run the gate, see the verdict, execute nothing).

**Operator auth:** every state-changing endpoint on `/governance`
(budget/reset, killswitch arm/disarm, approval submit/approve/reject/cancel,
`/check`) and `/flywheel` (turn start, approve, resume) requires
`Authorization: Bearer $MSB_OPERATOR_TOKEN` (503 until set, 401 on mismatch;
shared `api/auth.py` gate, constant-time). Reads stay open. Set idempotently
with `bash scripts/set-operator-token.sh` (status: `make governance-token`),
then restart. In-process CLIs need no token.

## Cockpit

`/cockpit` — one read-only screen over the whole system (services, guards,
hygiene gate, audit chain, vault/RAG freshness, research runs, memory, recent
errors). Self-contained page; data via `/cockpit/api` (parallel bounded probes,
per-panel error containment) and `/cockpit/find` (vault semantic search +
audit-chain match + research-run titles). Read-only; control stays on API/CLI.
`/` dashboard is a separate surface.

## Flywheel (Phase 2)

Research→Build loop, one turn end-to-end behind the Phase 0B brakes. Package
`msb_v3/flywheel/`, CLI `python -m msb_v3.flywheel` (`turn "PROBLEM"` / `status`
/ `show <id>` / `approve <id>` / `resume <id>` / `config`), HTTP `/flywheel/*`.
Turn parks at `WAITING_APPROVAL` at build/combine/record until approved (that's
the brake, not a bug). Charger/scanner internals: `CLAUDE.archive.md` → "Flywheel internals".

## Git

Dual-push: `origin` and `sovereign_intelligence_core` both point to `https://github.com/lordwilsonDev/msb-v3.git`.

**Pre-push hook:** `scripts/hooks/pre-push` runs `make portability` before any
push and blocks on failure (bypass: `MSB_SKIP_PORTABILITY=1 git push`). It gates
the working tree, so unrelated local edits can block a push. Hooks aren't
versioned — install once per fresh clone with `make hooks-install`.

**Tag immutability ruleset** (`release-tag-immutability`, ruleset `20801997`):
`refs/tags/v*` cannot be deleted or force-moved (binds everyone). Why there's no
required-status-check on tag creation, and the emergency path to remove a bad
release tag: `CLAUDE.archive.md` → "Tag ruleset".

## MCP

`msb-mcp-server` at `/Users/lordwilson/msb-mcp-server` provides `msb-v3.chat/memory_*`, `status`, `metrics_*` tools.

## Defaults

- Primary LLM: DeepSeek V4-Flash.
- Vault: `~/Documents/Vault` (git-tracked; override with `MSB_VAULT_PATH`). Repo
  root resolves from package/script location; override with `MSB_HOME` (Python)
  or `MSB_REPO` (shell scripts).
- Obsidian-first data; skills automate on top.
- `runtime/research/` is regenerated every run and never versioned; hand-written
  research notes go in `docs/`.
- Subsystem naming decoder (Triumvirate, Argus, Hippocampus, Hermes, Vesta,
  Ralph, Cockpit, …): `docs/glossary.md`.
