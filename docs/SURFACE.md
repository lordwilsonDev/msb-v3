# MSB v3 — Surface Area Map

**Close-out blueprint Phase 3 (FR-3.1 / AC-3.1).** Every `api/` router and every
`src/msb_v3/` subpackage is classified as one of:

- **LOAD-BEARING** — on the canonical path or daily ops; must stay green and
  actively maintained.
- **OPTIONAL** — a real, tested capability off the canonical path; maintained at
  a lower cadence, not part of the close-out bar.
- **FROZEN** — release-declared frozen (see `docs/releases/MSB-v3-RELEASE.md`):
  tests stay green, no new work accrues. `msb_ledger` and
  `personal_intelligence` are classified at the bottom as top-level packages.

Every classification is traced to a caller or a written decision; none is
intuition. `tests/docs/test_surface_map.py` enforces that no router or
subpackage is unclassified (AC-3.1).

## Routers (`src/msb_v3/api/`)

| Router | Class | Justification (traced) |
|---|---|---|
| `api/app.py` | LOAD-BEARING | The FastAPI composition root — mounts every router (close-out Phase 1 comment: the real runtime image entry point). |
| `api/agent.py` | FROZEN | The canonical `/agent/handle` path — release-declared frozen. |
| `api/auth.py` | LOAD-BEARING | Shared operator-auth gate (`api/auth.py`, constant-time compare) — every state-changing `/governance` and `/flywheel` endpoint depends on it (CLAUDE.md Phase 3). |
| `api/automation.py` | LOAD-BEARING | The automation brain's control surface (`/automation/*`, operator-gated) — the wake agent's automation hook and the /automation API both depend on it (docs/automation-brain.md). |
| `api/chat.py` | LOAD-BEARING | `/chat` surface; MCP bridge proxies to it (`mcp_bridge.py` case `chat`). |
| `api/codegraph.py` | OPTIONAL | Read-only repo intelligence; indexing operator-gated; no canonical-path caller (mcp_bridge `codegraph_*` only). |
| `api/console.py` | OPTIONAL | Operator console UI; release-declared "implemented and supported" but an operator surface, not the canonical path. |
| `api/context.py` | OPTIONAL | Context-engine compose endpoint (spec §4.2.3); no canonical-path caller. |
| `api/conversation.py` | LOAD-BEARING | Conversation contract — E2E-probed in CI (`factory-gate.yml` "Conversation E2E probe"). |
| `api/cron.py` | LOAD-BEARING | The scheduler's REST surface (README: "/cron — scheduled governed jobs"). |
| `api/dashboard.py` | LOAD-BEARING | `/cockpit` read-only observability + `/cockpit/find` vault search (README; CLAUDE.md Cockpit). |
| `api/evolution.py` | OPTIONAL | Evolution/research-growth surface; off canonical path. |
| `api/factory.py` | FROZEN | The factory pipeline surface — release-declared frozen. |
| `api/flywheel.py` | OPTIONAL | Flywheel turns — operator-driven, behind the Phase 0B brakes (CLAUDE.md Flywheel). |
| `api/governance.py` | LOAD-BEARING | The brakes' HTTP surface — every state change operator-token-gated (CLAUDE.md Governance brakes). |
| `api/graph.py` | OPTIONAL | Knowledge-graph endpoints; graph is "experimental" per completion blueprint Phase table. |
| `api/health.py` | LOAD-BEARING | `/health` liveness — the Docker HEALTHCHECK and every CI boot poll hit it. |
| `api/home.py` | LOAD-BEARING | `/` dashboard redirect (README: "/dashboard → /cockpit"). |
| `api/hook.py` | LOAD-BEARING | The webhook sense (`/hook/<automation_id>`) — the perceiver's single inbound endpoint every external platform points at; payloads queue to the wake inbox (docs/automation-brain.md, Stage 3). |
| `api/knowledge.py` | LOAD-BEARING | Ralph research surface; `ralph_loop_dashboard`/`ralph_loop_run` proxied from mcp_bridge. |
| `api/mcp_bridge.py` | LOAD-BEARING | The Make.com/HTTP MCP bridge (`/mcp/proxy`, `/mcp/tools`) — live-probed in CI auth gate. |
| `api/memory.py` | LOAD-BEARING | `/memory/{session}` — MCP bridge `memory_recent`/`memory_append`/`memory_clear` proxy to it. |
| `api/memory_fabric.py` | OPTIONAL | Memory-fabric surface (spec §4.2.2); off canonical path. |
| `api/metrics.py` | LOAD-BEARING | `/metrics/` + Prometheus scrape — the observability spine the cockpit and console read. |
| `api/models.py` | OPTIONAL | Model listing surface; no canonical-path caller. |
| `api/moie.py` | OPTIONAL | MoIE analysis surface; MoIE "exists and powers the factory reviewer" (release doc — experimental tier). |
| `api/notify.py` | LOAD-BEARING | `/notify` — the ops out-of-band alert surface (README ops section). |
| `api/openai_compat.py` | LOAD-BEARING | `/v1` OpenAI-compatible adapter — Open WebUI / OpenAI SDK path (README). |
| `api/rag.py` | LOAD-BEARING | `/rag/search` semantic vault search — used by mcp_bridge `search_query`, cockpit find, flywheel engine. |
| `api/research.py` | OPTIONAL | Research-assistant endpoints; off canonical path. |
| `api/safety.py` | LOAD-BEARING | Safety/guardrail surface; guardrails are wired into the tool loop (`guardrails/fold.py` used by DeepSeekClient). |
| `api/skill_router.py` | OPTIONAL | Skill discovery/execution from `~/.hermes/skills`; dormant-satellites disposition kept it deferred. |
| `api/smi.py` | OPTIONAL | SMI surface (SMI-017/018 workstreams); off canonical path. |
| `api/studio.py` | OPTIONAL | Studio surface; off canonical path. |
| `api/system.py` | LOAD-BEARING | `/system/health|config|routes` — deep health + truth-in-config surface (close-out Phase 2). |
| `api/tenant_chat.py` | OPTIONAL | Tenant chat routing — documented limitation "not tenant-scoped" (release doc #4); off canonical path. |
| `api/tenants.py` | OPTIONAL | Tenant admin surface; single-operator system (project-map §2). |
| `api/triumvirate.py` | LOAD-BEARING | The Triumvirate (Guardian/Argus/Hippocampus) — canonical-path governance (README safety model). |
| `api/wake.py` | LOAD-BEARING | The wake inbox/outbox channel (`/wake`, operator-gated) — the 5-minute resident agent's surface (docs/wake-loop.md). |
| `api/workflow.py` | OPTIONAL | Workflow surface; off canonical path. |
| `msb_v3/node/api.py` (router) | OPTIONAL | Sovereign Node `/node/v1/*` — first slice (scoped FILE_READ) is INTEGRATE phase (project-map). |
| `msb_v3/vesta/api.py` (router) | LOAD-BEARING | Vesta trust perimeter — `/vesta/*` signed-device approval paths, adversarially tested (13 bypass invariants). |
| `msb_v3/plei/api.py` (router) | OPTIONAL | PLEI project lifecycle intelligence — `/plei/*` ingestion and classification; Phase 1 (project twin). |

## Subpackages (`src/msb_v3/`)

| Package | Class | Justification (traced) |
|---|---|---|
| `msb_v3/agent` | FROZEN | The canonical governed loop — release-declared frozen. |
| `msb_v3/api` | LOAD-BEARING | Router layer — all of the table above. |
| `msb_v3/business` | OPTIONAL | Business-report surface (hygiene h02/h04 experiments probe it); off canonical path. |
| `msb_v3/codegraph` | OPTIONAL | Repo symbol graph (spec §4.2.1); operator-gated indexing, query-only use. |
| `msb_v3/conversation` | LOAD-BEARING | Conversation contract — CI E2E probe (factory-gate). |
| `msb_v3/core` | LOAD-BEARING | `config.py` settings — "env-var-first, every default declared there" (vault manifest). Everything imports it. |
| `msb_v3/cron` | LOAD-BEARING | The scheduler (README heartbeat section; 57 tests) — the wake-agent job rides it. |
| `msb_v3/automation` | LOAD-BEARING | The automation brain (budget + manifest + n8n/Make/Zapier/GHL clients) — wired into the wake runner's automation hook and the /automation API (docs/automation-brain.md). |
| `msb_v3/db` | LOAD-BEARING | SQLite infra under every store. |
| `msb_v3/device` | OPTIONAL | Signed-device enrollment support (Vesta path, low volume). |
| `msb_v3/evidence` | FROZEN | Evidence spine — release-declared frozen. |
| `msb_v3/fabric` | LOAD-BEARING | Model router + FrontierClient seam — `resolve_client` on the agent path; DeepSeekClient extends FrontierClient. |
| `msb_v3/energy_matrix` | OPTIONAL | Energy-aware resource scheduling — telemetry + scheduler + governance. |
| `msb_v3/factory` | FROZEN | The factory pipeline — release-declared frozen (dogfood reached MERGED). |
| `msb_v3/flywheel` | OPTIONAL | Research→Build loop — operator-driven, behind the brakes (CLAUDE.md Flywheel). |
| `msb_v3/gateway` | OPTIONAL | Capability Gateway dispatcher (provider-harness plan); no canonical-path caller yet. |
| `msb_v3/governance` | LOAD-BEARING | The brakes (kill switch, budgets, approvals) — fail-closed everywhere (CLAUDE.md). |
| `msb_v3/guardrails` | LOAD-BEARING | `fold.StepEnforcer` — wired into the governed tool loops (DeepSeekClient, conversation). |
| `msb_v3/harnesses` | OPTIONAL | Harness scaffolding; off canonical path. |
| `msb_v3/infrastructure` | LOAD-BEARING | Centralized environment contracts, including Qdrant preflight consumed by CI harness gates. |
| `msb_v3/local_ai` | LOAD-BEARING | Ollama + llama + DeepSeek clients — the model layer under `/chat`, `/agent`, `/v1`. |
| `msb_v3/memory` | LOAD-BEARING | SQLite session/message memory — `/memory` + MCP bridge depend on it. |
| `msb_v3/memory_fabric` | OPTIONAL | Memory fabric (spec §4.2.2); off canonical path. |
| `msb_v3/meta` | OPTIONAL | Meta-System project compiler — META-0: contract types only (`MetaTask`/`MSL`/`TaskState`/`ProjectState`/`VerificationResult`/`FailureRecord`/`WorkerResult`), no orchestration. Off canonical path. |
| `msb_v3/moie` | OPTIONAL | MoIE engine — powers the factory reviewer (release doc: experimental tier, not a general chat path). |
| `msb_v3/node` | OPTIONAL | Sovereign Node enrollment/engage — INTEGRATE phase, first slice FILE_READ. |
| `msb_v3/observability` | LOAD-BEARING | Prometheus metrics — `/metrics`, cockpit, console all read it. |
| `msb_v3/ops` | LOAD-BEARING | Ops module (backup/restore) — `make backup` / `make restore` targets. |
| `msb_v3/plei` | OPTIONAL | PLEI project lifecycle intelligence — Phase 1: project twin (ingestion + lifecycle classification). |
| `msb_v3/replay` | FROZEN | Replay engine — release-declared frozen. |
| `msb_v3/retrieval` | LOAD-BEARING | RAG/vector retrieval — `/rag` + flywheel novelty scan depend on it. |
| `msb_v3/speech` | OPTIONAL | Voice pipeline — STT, speaker verify, intent, TTS, voice response loop. |
| `msb_v3/runtime` | LOAD-BEARING | Runtime supervision (scripts/run.sh restart-on-exit); harness-gate depends on it. |
| `msb_v3/tasks` | OPTIONAL | Unified task document surface; off canonical path. |
| `msb_v3/tools` | FROZEN | The governed-tool registry behind the ActionGate — release-declared frozen. |
| `msb_v3/triumvirate` | LOAD-BEARING | Guardian/Argus/Hippocampus implementations (meta-cognitive planner is the real MoIE-plane). |
| `msb_v3/wake` | LOAD-BEARING | The resident wake loop (store + cycle runner) — the wake-agent cron action and /wake API depend on it (docs/wake-loop.md). |
| `msb_v3/uac` | FROZEN | Audit chain + ledger (P4 extraction) + anchor/notary — release-declared frozen; `msb_ledger` standalone. |
| `msb_v3/vesta` | LOAD-BEARING | Vesta trust perimeter — approval contracts adversarially tested (13 bypass invariants). |

## Top-level packages

| Package | Class | Justification (traced) |
|---|---|---|
| `msb_ledger` | FROZEN | Standalone hash-chain library, zero `msb_v3` imports (release doc P4; 30 guards in `tests/uac/test_ledger_extraction.py`). |
| `personal_intelligence` | OPTIONAL | Retained-deferred per dormant-satellites disposition (2026-08-13): `skill_engine` + `event_bus` kept, six dead modules archived. |

## Frozen marker convention (FR-3.2)

Frozen modules carry the class in this map and are excluded from
active-maintenance expectations; their tests stay green. The classification
itself is enforced by `tests/docs/test_surface_map.py` — a router or package
that appears on disk but not here fails the suite, so the map cannot silently
drift from the tree.
