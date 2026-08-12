# Phase 1 — The Cockpit: Implementation Plan

**Blueprint:** `docs/blueprints/2026-08-11-adaptive-build-environment.md` (§Phase 1)
**Decisions (owner, 2026-08-11):** build directly on `main` · server-rendered page, **no SPA** · `/cockpit` new (the existing `/` dashboard stays untouched) · find-box v1 = vault semantic search + audit-chain text match + research-run titles.
**Gate:** `make test` + `make portability` green, ruff clean.

## Why this phase exists

One screen that lets Wilson SEE the whole living system — services, models, governance brakes, audit chain, hygiene gate, vault index, research runs, recent errors — and find anything by asking. The backend for every panel already exists (blueprint §1); this is a thin, honest front-end over real data, built on the dashboard lessons already learned (parallel probes, loopback-pinned self-fetches, per-probe error containment — `home.py`).

## Global Constraints

- **Read-only.** No control actions on this page (kill-switch arm, approval decide, model switch stay on the API/CLI). The cockpit observes.
- **Self-contained page.** Inline CSS/JS, no CDN, no build step, works offline on the loopback host.
- **Every probe bounded and contained.** Parallel `asyncio.gather` with short per-probe timeouts (the home.py fix pattern); one dead panel never 500s the page or blocks the others.
- **Loopback-pinned self-fetches** (`http://127.0.0.1:{settings.port}`), never `settings.host` — same rule home.py learned through the Open WebUI proxy bug.
- **Path-portable.** All file reads derive from `settings.msb_home` (portability gate must stay green — no `/Users/...` literals).
- In-process reads (audit chain, governance, hygiene aggregate, log tail, research dirs, mission anchor) are used where the data lives; HTTP self-probes only for router surfaces. No second audit path, no new DBs.

## Task 1: `src/msb_v3/api/cockpit.py`

Three routes, one module (mirrors `home.py` conventions):

### `GET /cockpit` — the page
Single self-contained HTML document: dark glassmorphic theme (repo's cyber-teal aesthetic: `#66fcf1`/`#45a29e` accents), responsive card grid, no external assets. Panels:

1. **Focus strip (adaptive v1, rule-based):** foregrounds the active research run (from `/research/assistant/runs/_active`, else newest `runtime/research/` dir) and any recent errors in the server log. All-clear state when idle.
2. **Services** — `/health`, `/ready` (components), `/models/` (active model + backends).
3. **Mission** — `MissionAnchor.read()/verify()` (goal, phase, valid, scope hash) + Argus mulch tail (contained; empty when the DB is absent).
4. **Governance brakes** — kill switch pill, budget bars (research_calls/tokens/iterations with spent/limit), pending approvals count.
5. **Hygiene gate** — `artifacts/hygiene/hygiene_aggregate.json`: aggregate verdict + per-experiment chips (12).
6. **Audit chain** — `verify_chain()` validity + last ~8 events (component/event_type).
7. **Vault / RAG** — Qdrant direct: `tenant_wilson-vault` collection + point count (freshness).
8. **Research** — runs list (mtime-ordered) + active run + latest run status.
9. **Memory** — `/evolution/memory/summary` + `/evolution/memory/latest`.
10. **Recent errors** — `logs/server.log` tail, error/traceback/exception lines (last ~5).

Client JS: fetch `/cockpit/api`, render each panel with a skeleton while loading, per-panel error state, hover micro-interactions, auto-refresh toggle (15s) + manual refresh, live-status dot. No SPA framework.

### `GET /cockpit/api` — the data
JSON aggregation with **parallel, bounded, contained** probes: HTTP self-probes (`/status`, `/ready`, `/models/`, `/research/assistant/runs/_active`, `/research/assistant/latest`, `/safety/health`, `/knowledge/active-cluster`) via one `httpx.AsyncClient` + `asyncio.gather` (4s timeout each), plus in-process reads for hygiene/audit/governance/research-dirs/log-tail/mission/vault-freshness. Every panel is a dict; failures become `{"error": "..."}` — never a 500.

### `GET /cockpit/find?q=` — the find-box
Fan-out search, grouped result:
- **vault** — self `POST /rag/search` `{tenant_id: "wilson-vault", query: q, limit: 5}` → score/text/source.
- **audit** — in-process case-insensitive match over audit records (component/event_type/payload).
- **research** — `runtime/research/` run titles matching q.
Empty/missing `q` → 200 with empty groups (no server error).

## Task 2: app factory wiring

`app.include_router(cockpit_router, tags=["cockpit"])` (routes carry their own `/cockpit` paths). The existing `/` dashboard is untouched.

## Task 3: tests (`tests/api/test_cockpit.py`)

- `test_cockpit_html` — 200, HTML content-type, page markers present.
- `test_cockpit_api_shape` — 200; top-level keys (`services`, `mission`, `governance`, `hygiene`, `audit`, `vault`, `research`, `memory`, `errors`); every panel is a dict (error-containing is fine — proves containment without depending on a live server).
- `test_cockpit_find_shape` — `?q=sovereign` → 200 with `vault`/`audit`/`research` groups; empty `q` → 200 with empty groups.

## Self-Review

- No control surfaces on the page (read-only enforced by simply not exposing them).
- Probes bounded (4s) and parallel — a dead service costs one panel, not the page.
- Portability green: zero machine literals, all paths from `settings.msb_home`.
- Adaptive v1 is rule-based only — no ML, per blueprint §3.

## Not in this plan (explicit)

- **Control actions** (approve/arm/switch model) — API/CLI/Phase-2 cockpit build-mode only.
- **Harvest / inbox / promote** — Phase 2.
- **Multi-panel drill-down pages** — v1 is one screen; details expand in-page.
- **Auth on /cockpit** — loopback-bound like the rest; Phase 3 hardening.
- **Editing /dashboard or home.py** — untouched; this is a new, separate surface.
