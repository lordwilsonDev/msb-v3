# MSB v3 — Actual Architecture

## What This Project Is

MSB (Machine Sovereign Brain) v3 is a **governed AI agent runtime** with:
- Multi-provider agent execution (10+ providers behind one seam)
- MoIE (Model of Integrated Experts) — multi-expert AI reasoning
- ActionGate governance — every action is gated, audited, verifiable
- Evidence spine — hash-chained decision provenance
- PLEI (Project Lifecycle Engineering Intelligence) — 7-phase project intelligence
- Local AI infrastructure (Ollama, llama.cpp, Qdrant)
- Observability, repair, memory, retrieval, and automation subsystems

## Subsystem Map (by size)

| Subsystem | Files | Lines | Purpose | Connected | Tested |
|-----------|-------|-------|---------|-----------|--------|
| **plei** | 37 | 8,213 | Project lifecycle intelligence (7 phases) | ✅ | ✅ 31 tests |
| **api** | 40 | 7,533 | FastAPI app, routes, dashboards | ✅ Hub | ✅ 32 tests |
| **agent** | 15 | 5,130 | Agent providers, Paseo, handle loop | ✅ | ✅ 3 tests |
| **vesta** | 14 | 3,580 | Approval watchdog, node services | ✅ | ✅ |
| **ops** | 7 | 3,136 | Repair, auto-repair, root cause, verify | ✅ | ✅ |
| **conversation** | 5 | 2,631 | Envelope, model routing, stub mode | ✅ | ✅ 3 tests |
| **moie** | 10 | 1,790 | Multi-expert AI reasoning | ✅ | ✅ 6 tests |
| **automation** | 8 | 1,287 | Workflow automation | ✅ | ✅ 4 tests |
| **cron** | 5 | 1,258 | Scheduler, periodic tasks | ✅ | ✅ 4 tests |
| **factory** | 8 | 1,182 | Object construction | ✅ | ✅ 6 tests |
| **fabric** | 4 | 1,104 | Context engine | ✅ | ✅ 5 tests |
| **codegraph** | 5 | 1,082 | Code analysis, symbol graph | ✅ | ✅ |
| **tools** | 3 | 1,079 | Tool executors | ✅ | ✅ 6 tests |
| **governance** | 8 | 1,062 | Policy engine, risk templates | ✅ | ✅ 8 tests |
| **flywheel** | 5 | 993 | Self-improvement engine | ✅ | ✅ 6 tests |
| **local_ai** | 5 | 984 | Ollama, llama.cpp clients | ✅ | ✅ |
| **node** | 9 | 940 | Node management | ✅ | ✅ 4 tests |
| **core** | 7 | 897 | Config, container, calibration | ✅ Hub | ✅ |
| **triumvirate** | 5 | 866 | Three-agent orchestration | ✅ | ✅ 4 tests |
| **retrieval** | 5 | 849 | RAG, vector search | ✅ | ✅ 7 tests |
| **observability** | 4 | 765 | Audit, metrics, health | ✅ | ✅ |
| **memory_fabric** | 3 | 730 | Memory storage/recall | ✅ | ✅ |
| **tasks** | 4 | 660 | Task management | ✅ | ✅ |
| **evidence** | 2 | 582 | Evidence spine, hash chain | ✅ | ✅ 6 tests |
| **device** | 1 | 527 | Device management | ✅ | ✅ |
| **wake** | 2 | 417 | Wake/store | ✅ | ✅ 6 tests |
| **harnesses** | 2 | 412 | DeepSeek harness | ✅ | ✅ |
| **gateway** | 1 | 222 | Gateway | ✅ | ✅ |
| **runtime** | 1 | 201 | Runtime | ✅ | ✅ 7 tests |
| **replay** | 1 | 168 | Replay | ✅ | ✅ 4 tests |
| **integrations** | 1 | 136 | External integrations | ✅ | ✅ |
| **guardrails** | 1 | 131 | Guardrails | ✅ | ✅ |
| **business** | 1 | 102 | Business logic | ✅ | ✅ 4 tests |
| **memory** | 1 | 80 | Memory | ✅ | ✅ 4 tests |
| **db** | 1 | 57 | Database | ✅ | ✅ |

**Total: 264 source files, 43,911 lines, 213 test files, 2,020 tests collected**

## Entry Points

1. **FastAPI app** (`api/app.py:create_app()`) — main HTTP server on :8766
2. **PLEI CLI** (`plei/cli.py:main()`) — `python -m msb_v3.plei.cli`
3. **MoIE CLI** (`moie/cli.py:main()`) — multi-expert reasoning
4. **Flywheel CLI** (`flywheel/cli.py:main()`) — self-improvement
5. **Governance CLI** (`governance/cli.py:main()`) — policy management
6. **Ops CLI** (`ops/__main__.py:main()`) — repair, verify, diagnose
7. **Vesta watchdog** (`vesta/approval_watchdog.py`) — background approval monitor

## Key Integration Hubs

| Hub | Imports From | Role |
|-----|-------------|------|
| `api/app.py` | 49 msb_v3 modules | Wires all routers, startup/shutdown |
| `plei/api.py` | 47 modules | PLEI REST endpoints |
| `agent/handle.py` | 29 modules | Agent execution loop |
| `tools/executors.py` | 27 modules | Tool execution |
| `plei/orchestrator.py` | 27 modules | PLEI analysis pipeline |
| `api/system.py` | 21 modules | System health, repair, diagnosis |
| `core/container.py` | 17 modules | Dependency injection |

## What's Actually Exercised (Runtime)

The server starts on :8766. The main execution path:
1. Request → `api/app.py` → route handler
2. Agent requests → `agent/handle.py` → provider selection → tool execution
3. MoIE reasoning → `moie/engine.py` → expert synthesis
4. Governance → `governance/policy.py` → ActionGate → approval
5. Evidence → `evidence/spine.py` → hash chain
6. PLEI → `plei/orchestrator.py` → lifecycle analysis

## What's Verified (Tested)

- 2,020 tests collected
- All PLEI phases (1-7) have dedicated test suites
- API routes have integration tests
- Governance/ActionGate has contract tests
- Evidence spine has chain integrity tests
- Agent providers have unit tests

## What's NOT Verified

- No end-to-end integration test hitting the live server
- No load/performance tests
- No security penetration tests
- No disaster recovery tests
- Some subsystems have 0 dedicated tests (guardrails, integrations, gateway, runtime)
