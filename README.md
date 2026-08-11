# MSB v3

Sovereign local-first AI runtime: FastAPI + SQLite + Qwen3/Ollama + Prometheus.

## Endpoints

- `/chat` — POST `{"query": "...", "session": "default", "system": "...", "tools": [...]}`
  Tools are executed bounded (`max_steps=4`) and the response includes `history_count`.
- `/memory/{session}` — GET recent, POST append, DELETE clear
- `/metrics/` — JSON metrics summary
- `/metrics/prometheus` — Prometheus scrape
- `/system/health` — deep health check
- `/system/config` — runtime config, secrets masked
- `/system/routes` — live route registry
- `/status` — service/version/model/ready
- `/health` — liveness
- `/ready` — readiness
- `/dashboard` — unified studio HTML
- `/docs` — Swagger UI

## Env

See `.env.example`. Key vars:

- `MSB_HOST`, `MSB_PORT`
- `OLLAMA_URL`, `OLLAMA_MODEL`
- `MSB_DB_PATH`

## Open WebUI (ready-made chat UI)

MSB exposes an OpenAI-compatible `/v1` adapter so Open WebUI (or any OpenAI
SDK client) can drive the native harness. Setup, auth, and Tencent COS file
storage: [docs/open-webui-adapter-v1.md](docs/open-webui-adapter-v1.md).

## Run

bash scripts/start.sh

## Test

bash scripts/test.sh
