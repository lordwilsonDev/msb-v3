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

- `make test` / `make server` / `make smoke`
- `scripts/start.sh`, `scripts/stop.sh`, `scripts/run.sh`

## Ports

- msb-v3: `:8766`
- Ollama: `:11434`
- Qdrant: `:6333`

## Git

Dual-push: `origin` and `sovereign_intelligence_core` both point to `https://github.com/lordwilsonDev/msb-v3.git`.

## MCP

`msb-mcp-server` at `/Users/lordwilson/msb-mcp-server` provides `msb-v3.chat/memory_*`, `status`, `metrics_*` tools.

## Defaults

- Primary LLM: DeepSeek V4-Flash.
- Vault: `~/Documents/Vault` (git-tracked).
- Obsidian-first data; skills automate on top.
