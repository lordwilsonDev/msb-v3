# MSB v3

Sovereign local-first AI runtime: FastAPI + SQLite + Qwen3/Ollama + Prometheus.

## Endpoints

- `/chat` — POST `{"query": "...", "session": "default", "system": "...", "tools": [...]}`
- `/memory/{session}` — GET recent, POST append, DELETE clear
- `/metrics/` — JSON metrics summary
- `/metrics/prometheus` — Prometheus scrape
- `/health` — liveness
- `/ready` — readiness
- `/dashboard` — unified studio HTML
- `/docs` — Swagger UI

## Env

See `.env.example`. Key vars:

- `MSB_HOST`, `MSB_PORT`
- `OLLAMA_URL`, `OLLAMA_MODEL`
- `MSB_DB_PATH`

## Run

bash scripts/start.sh

## Test

bash scripts/test.sh
