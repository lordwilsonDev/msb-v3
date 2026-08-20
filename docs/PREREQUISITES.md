# PREREQUISITES — what you need to run msb-v3

The survival contract: a fresh machine should go from nothing to a running,
verified server by following this page. `make doctor` (`scripts/prereq-check.sh`)
checks every item below and prints PASS/FAIL/WARN with exit code 0 when all
critical prerequisites are present.

## The one-liner

```bash
make doctor   # prints exactly what's missing on YOUR machine
```

## Critical (server will not be fully functional without these)

| Prerequisite | What it does | Install |
|---|---|---|
| **Python ≥ 3.11** | the runtime | `brew install python@3.12` (macOS) / `apt install python3.12` (Debian) |
| **Python deps** | the app | `pip install -e ".[dev]"` from the repo root |
| **Ollama** | local model backend (default `qwen3:8b`) | https://ollama.com — then `ollama pull qwen3:8b` |
| **Qdrant** | vector store for vault search (`/rag`) | `docker run -d -p 6333:6333 qdrant/qdrant` or the included launchd agent |

## Optional (features light up as they're configured)

| Prerequisite | Feature it enables | Notes |
|---|---|---|
| **Docker** | the compose stack (Open WebUI, Timescale) | `docker compose -f docker-compose.sovereign.yml up -d` |
| **n8n** | the automation brain's first target (self-hosted, free) | reachable at `http://127.0.0.1:5678`; create an **API key** in n8n: Settings → API → `N8N_API_KEY` |
| **Make / Zapier / GoHighLevel keys** | remaining automation targets | `MSB_MAKE_WEBHOOK_URL`, `MSB_ZAPIER_API_KEY`, `MSB_GHL_API_KEY` |
| **`DEEPSEEK_API_KEY`** | the wake agent + automation brain (the $10 brain) | https://platform.deepseek.com — an OpenAI-compatible key; ~$10 = millions of tokens |
| **`OPENAI_API_KEY`** | the `/v1` adapter (Open WebUI chat) | `OPENAI_API_KEY` in `.env` |
| **`MSB_OPERATOR_TOKEN`** | `/cron`, `/wake`, `/automation` control surfaces | without it those endpoints are closed (503 — fail-closed by design) |
| **`MCP_BRIDGE_SECRET`** | live-auth gate on conversation/workflow routes | set it; unset = dev mode |

## Verify

```bash
make doctor                     # all critical PASS → you're ready
make server-start               # boot the server (or launchd runs it)
curl http://127.0.0.1:8766/health   # {"ok":true,...}
```

## Notes

- **Disk headroom:** the server needs ≥5% free disk to stay healthy; `make doctor`
  fails when you drop below it (the disk-health watchdog alerts at 85%).
- **The vault:** vault search needs the Obsidian vault indexed in Qdrant
  (`~/bin/vault-reindex.py` on this machine; see `docs/`).
- **Source license:** a bare pull is inert until you hold a license signed by
  the owner (see `docs/pull-signature-and-access.md`). `make doctor` assumes
  you're on the owner machine; CI/container runs declare `MSB_CI=1` /
  `MSB_CONTAINER=1` instead.
