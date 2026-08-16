# Forensic Build Audit — msb-v3 (2026-08-15)

Method: evidence over documentation. All claims below were verified against the live
server (PID 1916, :8766, v0.2.3), the running test suite, the SQLite stores, the
launchd registry, the Qdrant instance, and source reads. Nothing is credited because
a folder exists. Unverified claims are labeled UNKNOWN.

---

## 1. Executive Verdict

msb-v3 is a **real, running, tested local-first sovereign AI runtime** — not a
scaffold. A live server supervised by launchd, 950 passing tests, a hash-chained
audit ledger with **9,472 records**, a working Qdrant-backed RAG store, a durable
Vesta trust/approval perimeter, and a genuinely gated agent execution slice all
exist and were verified live.

It is **not** an autonomous multi-agent system. There is exactly one real agent
execution path (Handle → DAG → gated executor → verifier), it is model-dispatched
only through deterministic executor tools, and **model-advertised tool use via
`/chat` is structurally dead** (tools are advertised to the model but never
registered — every tool call resolves to `[tool-error] unknown tool`).

The single most serious finding is a **documented fabrication incident** in repo
history: commit `dd66dd3` shipped a "Phase 2 vertical slice" audit claiming
"53/53 tests passing" for files that **never existed on any branch**. It was
detected by the repo's own forensic review and removed by owner decision
(2026-08-10). The repo has since institutionalized anti-fabrication tooling
(`verify_claims.py`, the factory gate, daily gate evidence commits).

---

## 2. Repository Inventory

| Dimension | Reality |
|---|---|
| Location | `~/msb-v3`, 742 MB, ~5,900 files |
| Git | 331 commits, active through today (`b699e2d chore: daily gate evidence (PASS) 2026-08-15`) |
| Language | Python 3.11+, 30 package dirs under `src/msb_v3/` (~120 modules) |
| Web framework | FastAPI 0.141 + uvicorn |
| Package manifest | `pyproject.toml` — 7 runtime deps (fastapi, uvicorn, pydantic, httpx, prometheus-client, qdrant-client, cryptography); dev: pytest 9, pytest-asyncio, ruff, mypy, PyYAML. **No `[build-system]` table** (SMI-017 #8 still open) |
| Storage | SQLite (`data/msb_v3.db` 4.4 MB, `data/uac/audit_chain.db`, `data/vesta/{tasks,evidence}.db`, `data/node/audit_chain.db`); Qdrant (`tenant_wilson-vault` collection); JSON state dirs (`memory_graph/`, `triumvirate/`, `flywheel/`, `tenants/`, `truth/`) |
| Live services | Server :8766 (launchd `com.lordwilson.msb-v3` → `run.sh` supervisor), Qdrant :6333, Ollama :11434 (qwen2.5-coder:0.5b loaded), LM Studio :1234 (separate, dream-engine) |
| Automations | 6 launchd agents loaded (server, actions-runner, factory-gate, harness-evidence, chain-notary, backup); daily gate evidence committed to git |
| Docs | README, MANIFEST, extensive `docs/blueprints/`, `docs/audits/` (incl. prior SMI-017 forensic review), task contracts, open-webui adapter spec |

---

## 3. Actual Architecture (verified)

```
USER/HTTP
   │
   ▼
FastAPI app (:8766) — 30 routers, 146 routes mounted (OpenAPI-verified)
   │
   ├── /chat, /v1/*  ──► ChatHarness ──► Capability Gateway (route decision → audit)
   │                        │
   │                        └─► LocalAIClient.execute_tool_loop()  ◄── tools advertised,
   │                              │                                   NEVER registered → dead
   │                              └─► Ollama :11434 (qwen3:8b) / llamacpp
   │
   ├── /agent/handle ──► interpret → plan (DAG) → gated executor ──► BridgeProvider tools
   │                        │        │          │        │            (search_query→Qdrant,
   │                        │        │          │        │             chat→local model,
   │                        │        │          │        │             vault_write→file)
   │                        │        │          │        └─► verify (grounded, no LLM judge)
   │                        │        │          └─► REVIEW gate on tainted writes (fail-closed)
   │                        │        └─► hash-traced tasks
   │                        └─► ModelRouter (R-score) ──► frontier /v1 seam (OPENAI_API_KEY set)
   │
   ├── /vesta/* (52 routes) ──► signed intents (replay-protected) → durable task lifecycle
   │        → approvals (write + shell) → sandboxed FileReader/Writer/ShellExecutor
   │        → KillSwitch (fail-closed) → all events → AuditChain
   │
   ├── /rag/*, /graph/*, /retrieval/* ──► Qdrant + Ollama embeddings (nomic-embed-text)
   │        → vector / structural / temporal adapters, tenant-scoped collections
   │
   ├── /memory/* ──► SQLite message store (26,187 rows), session-scoped, token truncation
   │
   ├── /governance, /flywheel ──► require_operator (fail-closed bearer, 503 until set)
   │
   └── /conversation ──► retrieval→guard→compose pipeline (stub model profile for CI)
```

Every dispatch decision is recorded to the AuditChain (SQLite, hash-linked, externally
anchored via `chain_anchor.json` + `MSB_CHAIN_ANCHOR_KEY`). All boundary labels
VERIFIED unless noted.

---

## 4. Feature Reality Matrix

| Feature | Claimed | Code Exists | Wired In | Executable | Tested | Verified | Evidence |
|---|---|---|---|---|---|---|---|
| Chat API | yes | ✅ | ✅ | ✅ | ✅ | ✅ live (401 without secret, i.e. auth enforced) | `api/chat.py`, live probe |
| Model tool use (/chat `tools:`) | yes | ✅ loop | ⚠️ advertised | ❌ **dead** | ⚠️ unit-only | ❌ | `ollama.py:48` `register_tool`; **zero production callers**; `run_tool` → `[tool-error] unknown tool` |
| Agent slice (plan→execute→verify) | yes | ✅ | ✅ | ✅ | ✅ | ✅ | `agent/handle.py`, `executor.py`, `safety.py`, tests pass |
| Executor tools (search/chat/write) | yes | ✅ | ✅ | ✅ | ✅ | ⚠️ mocked | `agent/bridge_provider.py` real dispatch |
| Vesta trust perimeter | yes | ✅ | ✅ | ✅ | ✅ | ✅ live (`/vesta/status` ACTIVE, phase-0-2) | 27 tasks, 50 evidence, 8 approvals in SQLite |
| Signed device intents | yes | ✅ | ✅ | ✅ | ✅ | ⚠️ no live signed call made during audit | `vesta/api.py` signed-* routes |
| Audit chain | yes | ✅ | ✅ | ✅ | ✅ | ✅ 9,472 records, anchored | `data/uac/audit_chain.db`, `ledger/verify` |
| RAG / retrieval | yes | ✅ | ✅ | ✅ | ✅ | ✅ Qdrant live, collection exists | `api/rag.py`, retrieval adapters |
| Memory (chat history) | yes | ✅ | ✅ | ✅ | ✅ | ✅ 26,187 rows | `memory/store.py` |
| Model router (frontier/local) | yes | ✅ | ✅ | ✅ | ⚠️ mocked | ⚠️ seam configured; live frontier call UNKNOWN | `fabric/model_router.py`, `OPENAI_API_KEY` set |
| Governance (operator token) | yes | ✅ | ✅ | ✅ | ✅ | ✅ 200 live | `api/auth.py`, `/governance` |
| Multi-tenancy | yes | ⚠️ partial | ⚠️ | ⚠️ | ✅ | ⚠️ RAG tenant-scoped; chat LLM routing NOT tenant-scoped (documented) | `tenant_chat.py` placeholder comment |
| Multimodal interfaces | yes | ⚠️ **stub** | ⚠️ gated | ❌ | ⚠️ | ❌ | `triumvirate/multimodal_interfaces.py` returns `status:"stub"`; `MSB_MULTIMODAL_ENABLED` gate |
| Flywheel (self-improvement loop) | yes | ✅ | ✅ | ✅ | ⚠️ | ⚠️ default charger is `stub`; sovereign charger needs Tavily (key set in .env, live use UNKNOWN) | `flywheel/chargers.py:186` |

---

## 5. Agent Reality Matrix

```
Agent (Handle)         REAL  — agent/handle.py, mounted at /agent/handle
  ↓ Prompt             REAL  — interpret (LLM call to local model)
  ↓ Model              REAL  — Ollama qwen3:8b (local), frontier seam configured but live use UNKNOWN
  ↓ Tools              REAL  — BridgeProvider: search_query (Qdrant), chat (ChatHarness), vault_write (file)
  ↓ Permissions        REAL  — capability gateway + taint-aware REVIEW gate on writes, fail-closed
  ↓ Mutation           REAL  — vault_write writes real files under output dir; REVIEW-gated when tainted
  ↓ Result             REAL  — grounded file_written verifier reads artifact back; no LLM judge
```

**Break found:** the *other* advertised agent surface — model-emitted tool calls in
`/chat`, `/v1` (OpenAI-compat), and `/research` — has **no tool implementations
registered anywhere in production** (`register_tool` appears only in the two client
classes and tests). The loop is implemented and feeds results back (SMI-017 #12 is
fixed), but `run_tool()` returns `[tool-error] unknown tool` for every call. The
only working tool use is executor-driven (deterministic dispatch per DAG task),
which never touches the model tool-call loop.

---

## 6. API Reality Matrix

146 routes live (OpenAPI-verified, including all 52 `/vesta/*`). Notable:

| Route | Mounted | Auth | Reality |
|---|---|---|---|
| POST /chat | ✅ | ✅ `check_auth` (secret set → 401 live) | Real harness path; tools dead (see §4) |
| POST /v1/chat/completions | ✅ | ⚠️ inherits chat gate | OpenAI-compat; same dead-tools path |
| /vesta/* mutating routes | ✅ | ✅ `require_operator` + transport CIDR | Real |
| /vesta/* signed-* routes | ✅ | ✅ signed-intent verify (replay-protected) | Real, live use UNVERIFIED |
| /governance, /flywheel control | ✅ | ✅ `require_operator` fail-closed | Real |
| /rag, /memory, /tenants, /business | ✅ | ❌ reads open by design (SMI-017 #4 partial) | Real; **write paths on tenants/business still lack a route-level auth dependency** — not re-verified this pass, flag |
| POST /tenants (tenant_chat) | ❌ **NOT mounted** | — | Placeholder, honestly labeled, echoes input |

**Unreachable:** `tenant_chat.py` route (documented as placeholder). **Dead:** tool
registry (above). **Fail-open:** `/system/health` reports "healthy" while a backend
is unreachable; harness exception path returns `ok=True, event=chat:completed` with
`[fallback]` text.

---

## 7. Automation Reality Matrix

| Automation | Trigger | Runs unattended | Evidence | Reality |
|---|---|---|---|---|
| msb-v3 server | launchd `com.lordwilson.msb-v3` | ✅ PID 1222→run.sh→1916 | verified live | REAL, with crash-respawn loop (fixed `2665902`) |
| actions-runner | launchd `com.blackswanlabz.msb-v3.runner` | ✅ PID 1266 | launchctl | REAL |
| factory gate | launchd `com.blackswanlabz.msb-factory-gate` | ✅ daily gate evidence commit `b699e2d` (2026-08-15) | git log | REAL |
| harness evidence | launchd `com.blackswanlabz.harness-evidence` | ✅ | launchctl, plist | REAL |
| chain notary | launchd `com.lordwilson.msb-chain-notary` | ✅ | launchctl | REAL |
| backup | launchd `com.lordwilson.msb-backup` | ✅ | launchctl | REAL |
| qdrant-sweep / approval-watchdog | scripts (scheduled? UNKNOWN) | ⚠️ UNKNOWN | `scripts/*.sh` | scripts exist; scheduler not verified |

All launchd agents are **loaded** (verified via `launchctl list`). Retry/recovery
exists in the supervisor loop and client retry paths; the nightly factory gate
produces committed evidence.

---

## 8. Test Reality

- **950 passed, 3 skipped in 49.5 s** (`make test`, miniforge python) — executed this audit.
- **Real coverage of real paths**: harness gateway wiring, executor DAG, safety gates,
  verify, planner, BridgeProvider-shaped contracts, Vesta, uac, RAG adapters, retrieval.
- **Honest seams**: the `stub` model profile (`MSB_CONVERSATION_MODEL=stub`) is a
  documented, deterministic CI profile — real tests exercise the real pipeline with a
  zero-spend model. Not fake.
- **Caveats**: agent tests inject fake `generate()` clients (mocked model hop — expected);
  opt-in live tests (`test_live_*`) don't run by default; no test exercises a real
  model-emitted multi-tool sequence against a registered tool (because none exists);
  test collection still depends on Makefile `PYTHONPATH` (SMI-017 #8, no `[build-system]`).
- Prior false claim ("208/208 passed") from SMI-017 era is gone: artifacts removed,
  poison-pill state test-isolated, portability gate stages the repo to a temp path.

---

## 9. Security / Governance Reality

| Control | Reality | Evidence |
|---|---|---|
| Path traversal (3 instances) | **FIXED** (SMI-017 #1–3) | `_entity_path`/`_normalize_vault_path` containment + fail-closed secret; verified live in prior review |
| Chat auth | ENFORCED when secret set | live 401 |
| Operator control gate | fail-closed (503 unset / 401 mismatch / constant-time) | `api/auth.py`, live 200 |
| Audit chain | hash-linked, externally anchored | 9,472 records, `chain_anchor.json`, `MSB_CHAIN_ANCHOR_KEY` set |
| Vesta transport | CIDR admission (WireGuard 10.77.0.0/29 + loopback) | `/vesta/status` |
| Signed intents | replay protection, HMAC identity | `node/identity.py`, `EngageRequest` |
| Kill switch | fail-closed, unpartitioned (SMI-017 #11 open) | `governance/killswitch.py`, `triumvirate/guardian_scanner.py` |
| Shell execution | approval-gated, sandbox root, timeout, output cap | `vesta/shell.py` |
| Fallback masking | ❌ exceptions → `ok=True` `[fallback]` text | `harnesses/base.py` `_fallback` |
| Health truthfulness | ❌ "healthy" despite dead backend | live `/system/health` |
| Reads | open by design (documented) | SMI-017 #4 partial |

**Dangerous capability question — "can an agent execute this, what prevents it?":**
- Arbitrary file write: YES via `vault_write`/Vesta — prevented only by the REVIEW
  gate on tainted tasks and Vesta approvals + operator token. If the operator token
  is unset, Vesta mutating routes 503 (fail-closed) — good.
- Shell execution: YES via Vesta — gated behind explicit approval. Good.
- The `/chat` tool path: nothing to execute (dead registry) — safe by defect.

---

## 10. Memory / Data Reality

| Store | Writes | Reads | Persistence | Verified |
|---|---|---|---|---|
| SQLite `msb_v3.db` — `messages` | 26,187 rows, session-scoped | `/memory`, chat history | ✅ | ✅ |
| Qdrant `tenant_wilson-vault` | RAG index (embeddings) | `/rag`, `/retrieval/*` | ✅ | ✅ collection live |
| AuditChain `data/uac/audit_chain.db` | 9,472 hash-linked records | `ledger/verify` | ✅ anchored | ✅ |
| Vesta tasks/evidence | 27 tasks, 50 evidence, 8 approvals | `/vesta/*` | ✅ | ✅ |
| `memory_graph/*.json`, `triumvirate/*`, `truth/*` | JSON state | subsystems | ✅ (committed-state risk SMI-017 #10 partial) | ⚠️ |
| `msb_v3.db.audit_records` | **0 rows — vestigial** | none | — | dead table |

The INPUT → MEMORY → REASONING → ACTION → RESULT → MEMORY loop **exists** for the
agent slice (history → plan → tools → verify → audit) and is fully persisted.

---

## 11. Dead / Broken / Fake / Placeholder Findings

| Finding | Class | Material? |
|---|---|---|
| `/chat` + `/v1` + `/research` model tools never registered | **DEAD CAPABILITY** | Yes — advertised feature silently non-functional |
| `tenant_chat.py` echo placeholder, not mounted | SCAFFOLDED (honest) | Low — documented |
| `triumvirate/multimodal_interfaces.py` — `status:"stub"` | STUB (gated, honest) | Low — metrics exclude stub calls |
| Flywheel default charger `stub`; scanner offline fallback | PARTIAL (honest) | Medium — Tavily key now set, live use UNKNOWN |
| Harness exception → `ok=True` `[fallback]` | **SWALLOWED FAILURE** | Medium — callers can't detect degradation |
| `/system/health` "healthy" with dead backend | FAIL-OPEN reporting | Medium |
| `msb_v3.db.audit_records` empty table | DEAD CODE | Low |
| SMI-017 hand-authored `security_validation.json` (claimed nonexistent auth) | **FALSE ATTESTATION** — REMOVED | Resolved |
| `dd66dd3` fabricated "53/53 passing" Phase-2 audit | **FABRICATION** — documented, removed by owner | Resolved; anti-fabrication tooling now in place |
| `audit_chain.py` FIXME/TODO linter, `verify_claims.py`, portability gate | SELF-AUDIT TOOLING | Healthy |

---

## 12. Claim vs Reality

| Claimed Capability | Actual Implementation | Reality | Evidence |
|---|---|---|---|
| Autonomous agents | One deterministic plan→execute→verify slice | PARTIAL — no multi-agent, no autonomous loop | `agent/*` |
| Multi-agent orchestration | None exists | **NOT BUILT** | `sovereign_agent_factory_phase2.md` (the honest plan) |
| Memory | SQLite history + Qdrant RAG + audit chain | REAL | §10 |
| Planning | DAG planner with dependencies | REAL | `agent/planner.py` |
| Tool use | Executor tools REAL; model tools DEAD | MIXED | §5 |
| MCP | `mcp_bridge.py` (vault tools) + `/mcp` router | REAL, auth fail-closed | SMI-017 #2 fixed |
| Voice | none found | NOT BUILT | — |
| Remote execution | Vesta signed-intent transport (WireGuard CIDRs) | REAL, phase-0-2 | `/vesta/status` |
| Security | auth gating partial, traversal fixed, approvals real | GOOD with gaps | §9 |
| Governance | operator token + kill switch + audit chain | REAL | §9 |
| Self-healing | supervisor respawn + factory gate + retries | REAL | §7 |
| Evaluation | verify (grounded, no LLM judge) | REAL but shallow | `agent/verify.py` |
| Observability | Prometheus metrics + audit chain | REAL | `/metrics`, chain |
| Automation | 6 launchd agents + daily gate evidence | REAL | §7 |
| Multimodal | STUB-BACKED | **NOT BUILT** | `multimodal_interfaces.py` |

---

## 13. What FreeBuff Actually Built

> msb-v3 is a **local-first sovereign AI runtime**: a FastAPI server (146 routes)
> supervised by launchd, with a real Ollama-backed chat path, a hash-chained and
> externally anchored audit ledger (9,472 records), a Qdrant-backed tenant-scoped
> RAG store, a durable approval-gated Vesta trust perimeter (signed intents, shell
> and file approvals, kill switch), a deterministic agent slice (plan → gated
> execute → grounded verify) with real executor tools, a Prometheus metrics layer,
> and a 950-test suite that passes.
>
> It did **NOT** actually build: autonomous or multi-agent orchestration (only one
> deterministic slice), working multimodal interfaces (stub-gated), tenant-scoped
> chat routing (explicitly deferred), or a functioning model-driven tool-call
> surface (tools are advertised but never registered — dead).
>
> It **removed** its own worst artifacts: a fabricated audit commit and
> hand-authored false security attestations, replacing them with anti-fabrication
> gates that now produce daily committed evidence.

---

## 14. What Is Missing

1. **Tool implementations for the model tool-call loop** — the largest gap between
   claim and reality (register real tools or stop advertising them).
2. `[build-system]` in `pyproject.toml` (SMI-017 #8).
3. App-wide auth dependency on remaining routers (tenants/business write paths).
4. Partitioned kill switch (SMI-017 #11).
5. Honest health reporting (fail-closed, not "healthy").
6. Live verification of the frontier seam and sovereign flywheel charger.
7. Real multimodal backend or removal of the stub.

---

## 15. What Is Worth Keeping (HIGH VALUE)

- **Agent slice** (`agent/`, `fabric/`, `BridgeProvider`) — real, gated, hash-traced.
- **Vesta perimeter** — the most complete security engineering in the repo.
- **Audit chain + anchoring** — genuinely production-grade provenance.
- **RAG + retrieval adapters** — clean, tenant-scoped, live.
- **Local AI client layer** — bounded loops, think-stripping, retries.
- **Test suite (950)** and **anti-fabrication tooling** (verify_claims, factory gate, portability gate).
- **Automation** (launchd + supervisor + daily evidence).

## 16. What Should Be Rebuilt / Deleted

- **REBUILD**: the `/chat` tool surface — wire BridgeProvider-style tools into the
  client registry (one `register_tool` call site at app startup fixes it), or drop
  the `tools` param.
- **DELETE**: `tenant_chat.py` (or finish it per its own decision note),
  `msb_v3.db.audit_records` vestigial table, multimodal stub (or gate off entirely).
- **REPLACE**: harness `[fallback]` swallow with a `chat:degraded` event;
  `/system/health` with real backend probes.

---

## 17. Sovereign Stack Integration Map

| Layer | Status | Notes |
|---|---|---|
| Identity | PARTIAL | Vesta signed intents + node identity; no full identity layer |
| Intent | EXISTS | `agent/handle.py` interpret |
| Governance | EXISTS | operator gate, kill switch, audit |
| Policy | PARTIAL | `vesta/policy.py`, `governance/`; capability catalog exists |
| Planning | EXISTS | DAG planner |
| Agent Orchestration | PARTIAL | one slice; no multi-agent (honest plan exists) |
| Tool Execution | PARTIAL | executor tools real; model tools dead |
| Mutation | EXISTS | gated writes (REVIEW + approvals) |
| Verification | PARTIAL | grounded file verifier; no LLM judge |
| Audit | EXISTS | anchored hash chain — strongest layer |
| Memory | EXISTS | SQLite + Qdrant |
| Recovery | PARTIAL | supervisor respawn; no state replay |

**Reuse:** audit chain, Vesta approvals, executor DAG, RAG. **Wrap:** chat harness
(with registered tools). **Harden:** health reporting, auth coverage, partitioned
kill switch. **Delete:** stubs and the placeholder route.

---

## 18. Top 20 Highest-Value Findings

1. `/chat` model tools advertised but never registered — dead capability (fix = one startup registration).
2. Audit chain: 9,472 anchored hash-linked records — real provenance.
3. Vesta: durable approvals + signed intents + replay protection — real trust perimeter.
4. 950/950 tests pass; prior "208/208" false artifact removed.
5. Fabrication incident (`dd66dd3`) detected, documented, removed; anti-fabrication gates now enforce.
6. Agent executor: DAG + taint-aware REVIEW + fail-closed kill switch.
7. Harness exception → `ok=True [fallback]` — swallowed failure masks outages.
8. `/system/health` fail-open — "healthy" with dead backend.
9. SMI-017 #1–3 path traversals fixed and verified live; #4 auth partial; #8, #10, #11 open.
10. tenant_chat placeholder — honest but should be finished or deleted.
11. Multimodal interfaces are stub-backed and gated — not built.
12. Multi-tenancy: RAG tenant-scoped; chat LLM routing explicitly not.
13. Frontier seam configured (OPENAI_API_KEY) but live use UNVERIFIED.
14. 26,187 chat-history rows — memory store genuinely used.
15. 6 launchd agents + daily factory-gate evidence commits — automation is real.
16. Model router: deterministic R-score with honest degradation ("never fake the tier").
17. Retrieval adapters (vector/structural/temporal) — clean contract, lazy Qdrant.
18. `data/` state still partially committed (SMI-017 #10 partial).
19. Unpartitioned global kill switch (SMI-017 #11).
20. Repo's self-audit culture is exceptional: audits, reconciliation docs, verify tooling.

---

## 19. Recommended Next Engineering Actions

1. **Register real tools on the chat client at app startup** (BridgeProvider-backed)
   — turns the dead `/chat` tool surface into the advertised feature. Highest ROI.
2. Make `pyproject.toml` pip-installable (`[build-system]` + `pip install -e`), kill
   the Makefile PYTHONPATH dependency (SMI-017 #8).
3. Add `require_operator` to tenants/business write routes; keep reads open per policy.
4. Replace `[fallback]` swallow with a distinct `chat:degraded` event; make
   `/system/health` fail-closed per backend.
5. Partition the kill switch by agent/tenant (SMI-017 #11).
6. Finish or delete `tenant_chat.py`; gate-off or implement multimodal.
7. Add one live smoke test that exercises `/agent/handle` end-to-end against Ollama
   (un-mocked model hop) to close the "UNVERIFIED live frontier/local" gap.

---

## 20. Evidence Appendix

- Live server: PID 1916, `python -m msb_v3`, launchd `com.lordwilson.msb-v3` → `scripts/run.sh`; 146 routes from live OpenAPI; `/vesta/status` ACTIVE phase-0-2; `/governance` 200.
- Tests: `make test` → 950 passed, 3 skipped, 49.5 s (miniforge python).
- Tool registry: `src/msb_v3/local_ai/ollama.py:48` (`register_tool`), `llama_client.py:30`; **only** definitions + tests call it (`tests/test_api.py:275,300`, `tests/local_ai/test_ollama.py:107`); production call sites of `ChatHarness(`: `api/chat.py:67`, `api/openai_compat.py:278`, `api/research.py:224`, `agent/bridge_provider.py:187` — none registers tools. `run_tool` unknown-tool path: `ollama.py:66-68`.
- Tool loop feeds results back: `ollama.py:100-140` (`current_prompt` from history; SMI-017 #12 resolved since tag).
- Stores: `data/msb_v3.db` messages=26,187; `data/uac/audit_chain.db` records=9,472; `data/vesta/tasks.db` tasks=27 approvals=8; `data/vesta/evidence.db` evidence=50; Qdrant `tenant_wilson-vault` live; `chain_anchor.json` present, `MSB_CHAIN_ANCHOR_KEY` set in `.env`.
- Fabrication: `docs/audits/smi-017-forensic-review/RECONCILIATION.md` (dd66dd3 never-existed files; removed 2026-08-10); `docs/audits/smi-017-forensic-review/production_risks.md` (13 risks, fix status).
- Automations: `launchctl list` — 6 msb agents; git log `b699e2d` daily gate evidence PASS 2026-08-15; 331 commits.
- Placeholders/stubs: `api/tenant_chat.py:43` ("placeholder and is NOT mounted"); `triumvirate/multimodal_interfaces.py:10-27` (`status:"stub"`); `flywheel/chargers.py:186` (scanner stub).
- Auth: `api/auth.py` (`require_operator` fail-closed; `check_auth`), live `/chat` → 401.
- Swallowed failure: `harnesses/base.py` `_fallback` (`ok=True, event="chat:completed"`, `[fallback]` text).

---

## Phase 1 completion record — 2026-08-15 (unified-architecture §31)

All ten hardening items from the unified sovereign-agent architecture
Phase 1 are implemented and verified (978 passed, 4 skipped — up from 950):

| # | Item | Status | Evidence |
|---|---|---|---|
| 1 | Register governed chat tools | ✅ DONE | `src/msb_v3/tools/` (registry/runtime/executors); wired into `harnesses/base.py`; live `search_vault` returned real Qdrant hits |
| 2 | Tools terminate through the perimeter | ✅ DONE | capability gate + contained FileReader/FileWriter + audit per call; `vault_write` denied without `vault.write` (live) |
| 3 | No silent fallback | ✅ DONE | `ok=False, event="chat:degraded"`, structured `error`, failure class in telemetry; `/v1` + `/agent` not-ok branches now fire |
| 4 | Honest health | ✅ DONE | `/system/health` adds per-component `components` (api/db/ollama/qdrant/auditchain/vesta/governor) + derived `overall`; live all HEALTHY |
| 5 | Remaining write-route auth | ✅ DONE | `require_operator` on `/business/register|purge` and `/tenants/register|patch|delete`; live 401 unauthenticated, reads 200 |
| 6 | Partition kill switch | ✅ DONE | `KillSwitch` scoped tables + `is_blocked(scope_type, id)` + scope endpoints + ActionGate scope checks; global arm still blocks all |
| 7 | Gate tenant_chat | ✅ DONE | `tests/api/test_tenant_chat_gate.py` pins the placeholder is never mounted |
| 8 | Gate multimodal stub | ✅ DONE (was already) | live 503 unless `MSB_MULTIMODAL_ENABLED=1`; verified live |
| 9 | Real agent smoke test | ✅ DONE | `tests/agent/test_live_slice_smoke.py` — un-mocked model hop, skips unless `MSB_LIVE_TESTS=1` |
| 10 | `[build-system]` | ✅ DONE | `pyproject.toml` `[build-system]` (setuptools>=68) |

Bonus: fixed a latent double-prefix bug — `/tenants/tenants/register` →
`/tenants/register` (the gated write routes are now reachable at the
intended surface; pinned by `tests/api/test_tenants_router.py`).

Live deployment: launchd job restarted (new pid), all changes verified
against the running server (401 on unauthenticated business/tenants writes,
7-component health, scoped kill-switch endpoint operator-gated, `/chat`
auth unchanged).

---

## Phase 2 completion record — 2026-08-15 (unified-architecture §27-28)

Unified task object + event-sourced lifecycle implemented (994 passed, 4
skipped — up from 978):

- **`src/msb_v3/tasks/`** — `models.py` (UnifiedTask covering the full §27
  section map, JSON round-trip, DAG adapter), `events.py` (§28 event
  vocabulary + state machine + state→event mapping), `lifecycle.py`
  (durable sqlite task + event store, `create`/`emit`/`transition`/`update`,
  `recover_incomplete`, AuditChain mirror as the authoritative sequence).
- **Chain is the record, store is the projection** — same philosophy as
  `runtime/store.py`: every event mirrors to the chain (component="tasks",
  event_type="task.<EVENT>", audit_seq stored on the event row); a chain
  outage degrades provenance, never the run.
- **`EventingProvider`** — wraps any ToolProvider so tool-level events
  (TOOL_REQUESTED / TOOL_EXECUTED / MUTATION_COMMITTED / POLICY_CHECKED)
  flow into the lifecycle.
- **`agent/handle()` wired** — every run becomes a unified task: TASK_CREATED
  → INTENT_INTERPRETED → PLAN_CREATED → AGENT_STARTED → tool events →
  VERIFICATION_STARTED → VERIFICATION_PASSED/FAILED → EVIDENCE_RECORDED →
  TASK_COMPLETED/FAILED; the §27 document carries intent/plan/verification/
  evidence/outcome. All lifecycle writes best-effort (never break the run).
- **API** — `GET /agent/tasks`, `GET /agent/tasks/{id}`, `GET /agent/tasks/{id}/events`
  (operator-gated, consistent with /agent).
- **Live-verified**: a real `/agent/handle` run (verdict PASS, hash
  `65b2ae7479b080ac`) produced the full event sequence with chain seqs
  9473–9491; chain verifies valid at 9,491 records (19 added by the run).
- **Not started (Phase 2 remainder)**: provider abstraction + Paseo adapter,
  agent identity, capability-scoped agent permissions (spec §31 items 14-17).

---

## Phase 2 remainder completion record — 2026-08-15 (spec §31 items 14-17)

Provider abstraction + agent identity implemented (1013 passed, 4 skipped —
up from 994):

- **`agent/identity.py`** — `AgentIdentity` (durable, capability-scoped grant:
  granted_capabilities, tenant_scope, autonomy_level L0-L5, max_risk_tier;
  content-addressed `fingerprint` so a drifted grant is detectable) +
  `AgentRegistry` (sqlite, register/get/list/revoke, audit-chain events).
- **`agent/providers.py`** — `AgentProvider` ABC + `LocalAgentProvider`
  (the sovereign slice as a provider) + `CliAgentProvider` (Claude Code /
  Codex / OpenCode as a bounded, worktree-isolated subprocess worker —
  killed on timeout, output bounded, artifacts retrieved; honestly documented
  as best-effort isolation, not a sandbox — hence HIGH risk tier) +
  `ProviderRegistry` (deterministic select by capability + risk tier +
  availability).
- **Capability-scoped permissions (§17)** — `ActionGate.gate()` + `SafeProvider`
  accept a `granted` whitelist: a capability outside the agent's grant is
  BLOCKED fail-closed. `handle(agent_id=...)` resolves the identity; CLI
  agents get the whole task delegated (`_run_cli_agent`), local agents run
  the DAG path under their grant.
- **API** — `GET /agent/providers` (discovery), `POST /agent/register`,
  `GET /agent/agents[/{id}]`, `POST /agent/agents/{id}/revoke`; `/agent/handle`
  accepts `agent_id`. All operator-gated.
- **Live-verified**: provider discovery reports local.slice available and
  claude/codex/opencode unavailable (correctly); a registered read-only agent
  was blocked with `capability not granted to this agent: llm_synthesis`;
  a full-grant agent passed (PASS). Agent identity + event-sourced lifecycle
  interoperate (agent runs are unified tasks with the identity recorded in
  the agents section).
- **Not started (Phase 3)**: MoIE expert system (spec §31 items 18-24).

---

## Paseo adapter completion record — 2026-08-15 (spec §7, docs/paseo-adapter-v1.md)

MSB ↔ Paseo adapter implemented against the **real** Paseo repo (`~/paseo` @
`bf769158`) — every tool name verified in source (30 tools in
`agent/mcp-server.ts`), not guessed. Suite 1013 → **1037 passed**, 24 new
tests, deployed via launchd restart, live-verified.

- **`agent/paseo/client.py`** — MCP Streamable HTTP client (httpx, JSON-RPC 2.0,
  `mcp-session-id` sessions, SSE-tolerant body parsing, 404 → re-init + retry).
  The daemon mounts the surface at `/mcp/agents` (verified: `bootstrap.ts:417`,
  default `127.0.0.1:6767` per `config.ts` DEFAULT_PORT).
- **`agent/paseo/adapter.py`** — the six spec operations on the verified tools:
  `create_task` → `create_agent` (git worktree + initialPrompt, background),
  `assign_agent` → `create_agent` (provider/model), `send_task` →
  `send_agent_prompt`, `monitor` → `get_agent_status`/`wait_for_agent`,
  `interrupt` → `cancel_agent`/`kill_agent`, `retrieve_result` → status
  snapshot. `drive_run` is the governed end-to-end primitive: create in an
  isolated worktree → block on the daemon → **every permission request parks
  on an operator-gated Vesta approval** → only an approved decision is
  forwarded; denial interrupts the run; timeout stops the parked agent.
- **`agent/paseo/permissions.py`** — `PaseoPermissionBroker`: durable Vesta
  approvals (bind `paseo.<agent>.<request>`, payload sha256, PENDING →
  APPROVED/REJECTED, decided_by recorded). Single forwarding point: `decide()`
  records, forwards, and wakes the parked run — returning `(approval,
  forward_ok)` so callers report whether the response actually reached the
  daemon (no silent success).
- **`agent/providers.py`** — `PaseoAgentProvider` (kind `paseo`, HIGH risk
  tier 4, operator-registered, capability-gated) + `unavailable_reason()` on
  the ABC; `handle()` delegates kind `paseo` through the same worker path as
  CLI agents.
- **API (operator-gated)** — `POST /agent/paseo/create`, `/send`, `/interrupt`,
  `GET /agent/paseo/status/{id}`, `/permissions`, `POST
  /agent/paseo/permissions/{id}/respond`.
- **Health** — `/system/health` gained the `paseo` component (initialize
  handshake probe): daemon down → `FAILED` with the reason, overall derived
  (§14 honest health).
- **Live-verified** (daemon not running — the inert state is itself the test):
  health reports `paseo: FAILED unreachable: ConnectError`; providers list
  all three paseo seams `available=False`; create 422 validation;
  unauthenticated 401; a parked permission approved via the API records
  `APPROVED, decided_by=operator, forwarded=False` — the decision is durable
  and MSB says plainly the daemon wasn't reached.
- **Live-daemon verification (2026-08-15, later same day)** — `~/paseo`
  built and the daemon is now running under launchd
  (`com.lordwilson.paseo`, 127.0.0.1:6767, MCP at `/mcp/agents`, server
  `agent-mcp` v2.0.0). Build required two type-only patches in the fork
  (`claude-agent.ts`: SDK 0.2.141 narrowed `media_type` to a 4-value
  literal; stream-event cast) and a pnpm store snapshot refresh. Full
  production-path run verified: register paseo agent → `/agent/handle`
  with `repo=/tmp/paseo-scratch` → live `drive_run` → git worktree
  created in the scratch repo → claude worker spawned → permission
  parked as a durable Vesta approval and decided via the API → result
  retrieved. `/system/health` flipped `paseo: FAILED → HEALTHY`.

  **Live testing found and fixed three real bugs** (all now pinned by
  tests, suite 1037 → 1039):
  1. **Cross-instance wake**: the API constructs a fresh broker per
     request, so the operator's decision never reached the waiting run —
     the wait registry is now module-level (shared in the single-process
     uvicorn deployment).
  2. **Single-forwarder contract**: the decider forwarded nothing while
     the waiter assumed it did — the waiting run is now the one and only
     forwarder (allow, or deny+interrupt with an interrupt backstop).
  3. **Repo passthrough**: the delegation path never carried a target
     repo, so the worktree was created from the server's cwd (the first
     live run silently used the msb-v3 checkout) — `handle(repo=...)`
     now flows into the provider context and the trace.
  4. **Illegal lifecycle transitions**: the delegation path emitted
     CREATED→EXECUTING (rejected by the state machine, so state never
     advanced) — now walks CREATED→PLANNED→EXECUTING→VERIFYING→
     COMPLETED, verified live (`PLAN_CREATED, AGENT_STARTED,
     VERIFICATION_STARTED, TASK_COMPLETED` all present).

  Honest limitation recorded: the live claude worker returned "Credit
  balance is too low" (account has no credits — an external dependency),
  so the *machinery* is proven end-to-end while the model call is blocked
  by account state; the worker's output is exposed verbatim in the trace
  (output_head) — no silent success.
- **Observation streaming record (2026-08-15)** — worker activity now
  streams into the unified task. `PaseoAdapter.activity()` (the daemon's
  curated `get_agent_activity` timeline); `drive_run` samples it
  concurrently while `wait_for_agent` blocks (best-effort — a failed
  poll never breaks the run) and feeds each sample to an
  `on_observation` sink; the delegation path wires a lifecycle sink
  (`OBSERVATION_RECORDED` events + the §27 `observations` section, capped
  at 50); `GET /agent/paseo/activity/{id}` exposes the curated timeline
  (503 when the daemon is down). Suite 1039 → **1045 passed**.
  **Live-verified** against the real daemon: a handle() run recorded two
  `OBSERVATION_RECORDED` events during EXECUTING with real curated
  content ("Showing all 2 activities [User] Add a line to README.md…")
  in the task's observations section, and the activity endpoint returned
  the timeline (update_count=3).
- **SSE observation stream (2026-08-15)** — dashboards can now watch a
  run live. New `tasks/observations.py`: a process-wide pub/sub bus
  (bounded per-task subscriber queues, oldest-dropped on overflow,
  never raises — the same single-process-uvicorn assumption as the
  permission wait registry). The delegation-path observation sink
  publishes to the bus in addition to the lifecycle record. New
  `GET /agent/tasks/{task_id}/observations/stream` (SSE): replays the
  task's recorded observations, then streams new ones live, emitting
  `event: done` when the task reaches a terminal state (2s poll cadence
  doubles as the keepalive), with guaranteed subscriber cleanup on
  disconnect. Auth: `require_operator_sse` — bearer header for fetch
  clients, `?token=` for EventSource (browsers cannot set headers);
  unknown task -> 404. Suite 1045 → **1053 passed**. **Live-verified**
  against the real daemon: a handle() run streamed two live
  `event: observation` frames (real curated daemon content — the prompt
  + worktree setup) then `event: done {state: COMPLETED}`, matching the
  two OBSERVATION_RECORDED events durably recorded in the task.
  Lifecycle walked the full legal path
  (TASK_CREATED → PLAN_CREATED → AGENT_STARTED → OBSERVATION_RECORDED ×2
  → AGENT_COMPLETED → VERIFICATION_STARTED → VERIFICATION_PASSED →
  EVIDENCE_RECORDED → TASK_COMPLETED). Worker reported "Credit balance
  is too low" (external account state) — exposed verbatim, no silent
  success. Test agents archived, worktrees cleaned.
- **Code Graph subsystem (2026-08-15)** — Sovereign Architecture v4.0
  §4.2.1 (P0) Phase 1 executed: repository intelligence so agents answer
  symbol queries without loading files. New `codegraph/` package —
  `schema.py` (node kinds + edge relations, SQLite DDL), `store.py`
  (plain SQLite graph tables, provenance on every node/edge),
  `parser.py` (stdlib-only: real `ast` for Python, per-language regex
  heuristics for js/ts/go/rust/etc. flagged `approximate` — the honest
  zero-new-dependency choice), `indexer.py` (repo scan → parse →
  upsert, module containment wiring), `queries.py` (find_symbol,
  callers_of, callees_of, references_of, impact_of, context_of,
  rename_preview). Governed read-only tools registered:
  `codegraph.explore/context/impact/rename` (LOW risk, NONE mutation)
  terminating inside the perimeter via `tools/executors.py`. Operator-
  gated `/codegraph/*` API (index + 7 query routes, `{repo:path}`
  converter). Suite 1053 → **1098 passed**. **Live-verified** against
  the deployed server: indexed msb-v3's own src (161 files, 1514
  nodes, 10602 edges), callers/context/impact/rename queries all
  answered in **~40ms** (validation gate G1: <1s), rename preview of
  `require_operator` → 1 def + 63 references, impact of handle.py →
  the real 4-symbol call chain, 401 without the operator token.
  **Bug found & fixed by the new tests**: `register_governed_tools`
  late-bound `tool_id` in its closure — registering two tools silently
  routed every call to the LAST one. Bound as a default arg; pinned by
  test_runtime_gate_audits_codegraph_call.
- **Code Graph MCP exposure (2026-08-15)** — the five read-only codegraph
  tools are now callable over the existing MCP bridge (`POST /mcp/proxy`,
  same `x-mcp-secret` gate as every bridge tool): `codegraph_stats`,
  `codegraph_explore`, `codegraph_context`, `codegraph_impact`,
  `codegraph_rename` — added to the `/mcp/tools` manifest and dispatched
  in-process against the local SQLite graph (same containment as
  vault_read: no source-tree access, no network hop). Indexing stays
  operator-gated at `POST /codegraph/index`; the bridge is queries only.
  Suite 1098 → **1107 passed**. **Live-verified** through the deployed
  bridge: manifest lists all five; explore resolves `require_operator`
  (+`_sse`); context resolves `create_app`'s caller; impact reports 8
  seeds / 4 dependents for handle.py; rename previews 63 references;
  401 without the secret. (Note: index `nodes` counts symbols; stats
  counts all rows incl. per-file file/module nodes — 1521 vs 1843 for
  msb-v3, both consistent, no drift.)
- **Memory Fabric (2026-08-15)** — Sovereign Architecture v4.0 §4.2.2
  (P0) executed: durable cross-session agent memory with provenance,
  verification states, and decay. New `memory_fabric/` package —
  `models.py` (MemoryType episodic/semantic/procedural/architectural,
  VerificationState UNVERIFIED→VERIFIED→CONTRADICTED→DEPRECATED with
  legal-transition map, `live_score` = importance × recency — spec §17
  decay model), `store.py` (SQLite `memory_items` with full provenance
  columns + `verification_history` audit table; soft delete keeps the
  row), `fabric.py` (store/recall/verify/forget/consolidate). Recall is
  deterministic keyword scoring with a best-effort Qdrant semantic
  boost (thread-bridged, never breaks recall); consolidation merges
  duplicate memories and decays importance by recency. Governed tools:
  `memory.recall` (LOW/NONE) + `memory.store` (MEDIUM/WRITE,
  `memory.write` capability required — denied by default). Operator-
  gated `/memory-fabric/*` API (store/recall/verify/forget/consolidate/
  stats/{id}). MCP bridge: `memory_store/recall/verify/forget/consolidate`
  tools in the manifest + dispatch. Suite 1107 → **1138 passed**.
  **Live-verified** against the deployed server: stored two architectural
  memories (importance 0.9/0.6 — recall ranked 0.9 first), verified M1
  → VERIFIED with full audit record (by/reason/timestamp), consolidate
  merged the duplicate (M2 → DEPRECATED, relationship on M1), stats
  showed 1 active/1 archived/2 transitions, MCP bridge recall returned
  the consolidated VERIFIED memory, 401 without the token. Test
  memories archived after verification.
- **Context Engine (2026-08-15)** — Sovereign Architecture v4.0 §4.2.3
  (P1) executed: layered L0-L7 context composition on top of the Memory
  Fabric. New `fabric/context_engine.py` — `ContextEngine.compose(task,
  tenant, session, repo, project, tech, budget)` runs 8 layers, each
  best-effort and injectable: L0 system invariants (always), L1 task
  (always), L2 repo structure + L3 surgical code snippets (Code Graph,
  never whole files), L4 memories (Memory Fabric recall), L5 skills
  (registry description match), L6 history (AuditChain recent task
  events), L7 research (pluggable slot, OFF by default — never silently
  invoked). Hard budget model: required layers L0/L1 always fit (each
  guaranteed a share), optional layers capped per-layer and evicted
  bottom-up under the hard total; every response carries a per-layer
  ledger (requested/included/evicted/reason) and the naive un-budgeted
  baseline for validation gate G3. Governed tool `context.compose`
  (LOW/NONE), operator-gated `GET /context/compose`, MCP bridge tool
  `context_compose`. Suite 1138 → **1160 passed**. **Live-verified**
  against the deployed server: composed a real context over the seeded
  fabric + indexed codegraph — 8 layers composed, L2 found
  `require_operator @ auth.py:64`, L4 pulled the seeded fabric memory,
  L5 matched installed skills, all inside a 2000-token budget; MCP
  proxy composed the same context (275 tokens) with `require_operator`
  in the text; 401 without the token. Test memory archived after.
- **Not started**: Phase 3 MoIE (spec §31 items 18-24); Agent Sandbox
  (P1) and Software Factory (P3) remain.

## Update — Context Engine wired into the delegation path (2026-08-15)

The Context Engine now composes every **Paseo** delegation: `handle()` →
`_run_delegated_agent` composes the task (L0-L7, budgeted, best-effort)
and the worker's `initialPrompt` becomes the composed context + the raw
task verbatim. The composition ledger (layers, tokens, G3 reduction) is
recorded as a new canonical **CONTEXT_COMPOSED** task event (added to
`tasks/events.py`) and in the task's `context.composed` section; it also
rides in the provider context for observability. Injectable via a new
`context_engine` parameter on `handle()` (tests pin semantics with a fake
engine); a composition failure degrades to the raw request — the run
never breaks (best-effort, per the no-silent-fallback rule it logs the
warning instead).

- **Files**: `agent/handle.py` (`_run_delegated_agent` + `handle`),
  `tasks/events.py` (CONTEXT_COMPOSED in the canonical vocabulary).
- **Tests**: +2 in `tests/agent/test_providers.py` (composed goal +
  event + ledger; best-effort failure). Suite 1160 → **1162 passed**.
- **Live-verified** against the deployed server + real Paseo daemon:
  registered `paseo-ctx-demo`, ran `/agent/handle` with a real worktree
  agent. The worker's actual initial prompt (from the daemon activity
  feed) started with the composed context — L0 invariants, L1 task,
  L5 skills — and the task lifecycle recorded
  `TASK_CREATED → PLAN_CREATED → AGENT_STARTED → CONTEXT_COMPOSED →
  OBSERVATION_RECORDED×2 → AGENT_COMPLETED → VERIFICATION_PASSED →
  TASK_COMPLETED` (8 layers, 190 tokens). Demo agent revoked, daemon
  agent killed after.
- **Scope note**: composition is Paseo-only by design (the request was
  "every Paseo run"); CLI workers still receive the raw task.
- **Not started**: Phase 3 MoIE (spec §31 items 18-24); Agent Sandbox
  (P1) and Software Factory (P3) remain.

## Update — CONTEXT_COMPOSED consumer: composed contexts persist to the Memory Fabric (2026-08-15)

The composed context handed to a Paseo worker is now persisted into the
Memory Fabric as an **architectural** memory once the run completes —
`_run_delegated_agent` calls a new best-effort `_persist_composed_context`
(after `AGENT_COMPLETED`, paseo-only by construction since `package` is
only set on that path) with full provenance: content = the composed
package text, `type=architectural`, tags `[context-composed,
paseo.<provider>, delegation]`, importance 0.7, `source_agent` = the
agent identity, `task_id` = run_id, tenant + project (repo). Success
emits the canonical **MEMORY_STORED** event; a storage failure logs and
degrades provenance — never the run. Injectable `memory_fabric` param on
`handle()` (tests use a fake fabric; hermetic).

- **Files**: `agent/handle.py` (`_persist_composed_context` + wiring).
- **Tests**: +1 net (compose-context test now asserts the persisted
  memory + MEMORY_STORED; new best-effort failure test). Suite 1162 →
  **1163 passed**.
- **Live-verified** against the deployed server + real daemon: ran
  `/agent/handle` with `paseo-mem-demo`; the task events show
  `… AGENT_COMPLETED → MEMORY_STORED {memory_id: 0d22119538e247a4,
  architectural, paseo-delegation} → VERIFICATION_PASSED → TASK_COMPLETED`
  and `/memory-fabric` recall (query "paseo delegation") returns the
  composed context at score 0.7 (architectural/UNVERIFIED, src
  paseo-mem-demo). Test memory forgotten, demo agent revoked, daemon
  agent killed after.
- **Not started**: Phase 3 MoIE (spec §31 items 18-24); Agent Sandbox
  (P1) and Software Factory (P3) remain.

## Update — Consolidation pass after every Paseo run (2026-08-15)

The CONTEXT_COMPOSED consumer now ends with a **consolidation pass**: after
the architectural memory is stored, `_run_delegated_agent` calls a new
best-effort `_consolidate_composed_memories` → `fabric.consolidate(tenant,
by="paseo-delegation")`, merging near-duplicate architectural memories
(same project + type + shared tag) and decaying every active memory by
recency. The honest report ({merged, deprecations, decayed}) is recorded
as a new canonical **MEMORY_CONSOLIDATED** task event (added to
`tasks/events.py`). Runs after MEMORY_STORED only; best-effort — a
failure logs and degrades provenance, never the run.

- **Files**: `agent/handle.py` (`_consolidate_composed_memories` +
  wiring), `tasks/events.py` (MEMORY_CONSOLIDATED).
- **Tests**: +1 net (compose-context test now asserts the consolidation
  ran with the run's tenant + report fields; new best-effort failure
  test). Suite 1163 → **1164 passed**.
- **Live-verified** against the deployed server + real daemon: two
  `/agent/handle` runs on the same repo. Run 2's lifecycle shows
  `… MEMORY_STORED → MEMORY_CONSOLIDATED {merged: 1, deprecations:
  [cc7f13e7…], decayed: 0} → VERIFICATION_PASSED → TASK_COMPLETED`. The
  fabric now holds one survivor (run 2's memory, newest survives the
  stable sort) with run 1's DEPRECATED + archived and the relationship
  recorded. Bonus finding: run 2's composed package legitimately embedded
  run 1's memory via its L4 layer (score 0.70) — fabric recall working
  inside the Context Engine. Test memory forgotten, demo agent revoked,
  both daemon agents killed after.
- **Not started**: Phase 3 MoIE (spec §31 items 18-24); Agent Sandbox
  (P1) and Software Factory (P3) remain.

## Update — Context composition + memory persistence extended to CLI workers (2026-08-15)

Delegated runs now compose context and persist it for **both** worker
kinds. The composition gate in `_run_delegated_agent` widened from
`kind == "paseo"` to `kind in ("cli", "paseo")`; the persist +
consolidate block was already gated on `package is not None`, so CLI
workers now get the same lifecycle: composed initial prompt (worker goal
= composed package + raw task), CONTEXT_COMPOSED event + ledger,
architectural memory persisted after the run (MEMORY_STORED, source
"delegation"), consolidation pass (MEMORY_CONSOLIDATED). Provenance tags
now carry the real provider id (`cli.claude`, `paseo.claude` — was a
hardcoded `paseo.` prefix), and the consolidate `by` label is neutral
("delegation"). Local agents never reach this branch (unchanged).

Also fixed a deployment gap found during live verification: the msb-v3
launchd plist set no PATH, so the deployed server could never run CLI
workers (claude/codex/opencode all "not on PATH" even though installed).
Added `EnvironmentVariables.PATH` (/opt/homebrew/bin + ~/.local/bin +
defaults) to `~/Library/LaunchAgents/com.lordwilson.msb-v3.plist` and
reloaded — all three CLI providers now report available.

- **Files**: `agent/handle.py` (gate + tags + `by` label), launchd plist.
- **Tests**: CLI delegation test now pins composition + persistence
  (hermetic fakes; asserts composed goal, `cli.w` tag, source_agent,
  task_id, MEMORY_STORED/CONSOLIDATED). Paseo lifecycle + observation
  tests made hermetic (previously wrote real memories to the fabric DB on
  every suite run — a pollution bug from the persist feature). Suite
  stays **1164 passed**.
- **Live-verified**: registered `cli-ctx-demo`, `/agent/handle` ran a real
  `claude -p` subprocess (7.7s, worker exited 1 — provider account has no
  credits, same as the paseo live test). Lifecycle shows `… CONTEXT_COMPOSED
  → AGENT_COMPLETED → MEMORY_STORED {c7dda245, architectural, delegation}
  → MEMORY_CONSOLIDATED {merged: 0} → VERIFICATION_FAILED → TASK_FAILED`
  (FAIL is the worker, not the wiring) and the fabric holds the memory
  tagged `['context-composed', 'cli.claude', 'delegation']`. Test memory
  forgotten, demo agent revoked after.
- **Not started**: Phase 3 MoIE (spec §31 items 18-24); Agent Sandbox
  (P1) and Software Factory (P3) remain.

## Update — Observation sink for CLI workers (2026-08-15)

CLI subprocess stdout now streams into the unified task as observations,
matching the Paseo activity sink. `handle()` wires the observation sink
for both kinds (`kind in ("cli", "paseo")`), and `CliAgentProvider.execute`
replaced the final `proc.communicate()` with a streaming drain loop: each
non-empty stdout line becomes an `{source: "cli.output", observed_at,
update_count, content}` sample awaited into the sink (OBSERVATION_RECORDED
event + §27 observations + SSE live channel). Total captured output stays
bounded at `_MAX_OUTPUT_BYTES`; the timeout/kill path is unchanged; a
sinking failure logs and continues — never breaks the worker run. Fixed a
missing `datetime` import in providers.py (ruff caught it).

- **Files**: `agent/providers.py` (streaming drain), `agent/handle.py`
  (sink gate widened).
- **Tests**: +2 (streaming unit test: 3 lines → 3 samples with source
  cli.output, update_count 1..3, timestamps; sink-failure best-effort
  test). CLI delegation test now asserts the worker's stdout landed as an
  observation (`WORKER OK`). Suite 1164 → **1166 passed**.
- **Live-verified**: `/agent/handle` with `cli-obs-demo` ran a real
  `claude -p` subprocess; the task now records
  `AGENT_STARTED → CONTEXT_COMPOSED → OBSERVATION_RECORDED → AGENT_COMPLETED
  → MEMORY_STORED → MEMORY_CONSOLIDATED → VERIFICATION_FAILED → TASK_FAILED`
  with observation `[1] cli.output: "Credit balance is too low"` (timed —
  streamed live, not post-hoc). Test memory forgotten, demo agent revoked
  after.
- **Not started**: Phase 3 MoIE (spec §31 items 18-24); Agent Sandbox
  (P1) and Software Factory (P3) remain.

## Update — Funded CLI delegation: no funded provider exists; PASS+streaming confirmed with real seams (2026-08-15)

Requested: a real end-to-end CLI delegation with a funded provider to
confirm a PASS verdict with streaming. **Finding: no funded provider
account exists on this box.** Evidence:

- `claude auth status`: logged in via claude.ai (firstParty managed key,
  wilsonlord241@gmail.com) but `subscriptionType: null` → every `claude -p`
  run returns "Credit balance is too low" (exit 1). No credits.
- `codex`: `~/.codex/auth.json` exists but the binary is broken
  (`ENOENT` on the vendored aarch64 binary in the npm package).
- `opencode`: no auth.json, no config.
- `OPENAI_API_KEY` in `.env` (exported to the server by run.sh): rejected
  by `api.openai.com/v1/models` — "Incorrect API key provided".

**What was confirmed instead** — a real PASS verdict + live streaming
through the production path with all real seams: `agent.handle.handle()`
with a real `CliAgentProvider` (a real local script binary — the only
substitution for a funded model, which is exactly the provider contract),
real ContextEngine (live codegraph + fabric retrieval), real MemoryFabric
persist + consolidate, real TaskLifecycle + AuditChain. Result:

```
ok: True | verdict: PASS
events: TASK_CREATED → PLAN_CREATED → AGENT_STARTED → CONTEXT_COMPOSED →
  OBSERVATION_RECORDED ×4 → AGENT_COMPLETED → MEMORY_STORED →
  MEMORY_CONSOLIDATED → VERIFICATION_STARTED → VERIFICATION_PASSED →
  EVIDENCE_RECORDED → TASK_COMPLETED
observations (streamed live, timed): "starting work", "analyzing inputs",
  "writing result", "done" — all source=cli.output
artifact: result.txt (13 bytes) retrieved from the worktree
```

Test memory forgotten, temp files removed. To get a real-model PASS the
operator must supply a funded account: a valid `ANTHROPIC_API_KEY` (or
funded claude.ai subscription), a working `codex` install + funded plan,
or a valid `OPENAI_API_KEY`. Nothing in code blocks it — every provider
fails loudly with evidence, per the no-silent-fallback rule.

- **Not started**: Phase 3 MoIE (spec §31 items 18-24); Agent Sandbox
  (P1) and Software Factory (P3) remain.

## Update — Phase 3 MoIE built: expert registry, inversion pipeline, router, evidence merger, contradiction detector, meta-critic, IDS (2026-08-15)

The full Mixture-of-Inversion-Experts subsystem (spec §3, §23-25; §31
items 18-24) is implemented, tested, deployed and live-verified:

- `moie/models.py` — Assumption (text/kind/confidence + inversion +
  risk), ExpertReport, Contradiction, IDS, MoIEDecision (verdict
  APPROVE/CONDITIONAL/BLOCK + `blocked` = the §25 inversion-gate surface).
- `moie/experts.py` — Expert interface (pluggable; an LLM-backed domain
  expert only implements `analyze`) + ExpertRegistry + ten deterministic
  `DomainExpert`s (security, reliability, adversarial — the always-on
  safety floor — plus architecture, economic, operational, governance,
  human-factor, data-memory, domain). DomainExpert is deliberately a plain
  class so subclass class-attribute overrides work (the dataclass gotcha
  was caught by a test and fixed).
- `moie/pipeline.py` — assumption extraction from the claim's own signal
  phrases (explicit) + deontic modals (implicit), inversion ("what if the
  opposite holds?"), falsifiable predictions, causal alternatives.
- `moie/router.py` — safety floor + keyword-matched experts; `domains` /
  `thorough` context knobs; deterministic registry order.
- `moie/merger.py` — best-effort memory-fabric recall grounds every
  report (evidence ids attach to each); failure → honest zero, never
  breaks analysis.
- `moie/meta_critic.py` — fail-closed (any BLOCK blocks), pairwise
  contradiction detector (BLOCK-vs-SAFE/CONCERN, confident CONCERN-vs-
  SAFE), confidence degraded 0.15 per material contradiction, IDS
  composite (7 components, weighted 0..1).
- `moie/engine.py` — MoIEController.analyze() wiring it all; a broken
  expert fails closed to CONCERN with an honest summary.
- Surfaces: governed tool `moie.analyze` (LOW/NONE), operator-gated
  `POST /moie/analyze` + `GET /moie/experts`, MCP bridge `moie_analyze`.

**Tests**: +27 (13 engine + 14 surface). Suite 1166 → **1193 passed**.
Notable catches: dataclass-subclass expert override clobbering; empty
claim synthesizes APPROVE with no reports (nothing assessed); operator
gate 503-when-unconfigured vs 401-on-mismatch.

**Live-verified** against the deployed server:
- `GET /moie/experts` → 10 experts.
- Danger claim ("Disable auth and bind 0.0.0.0…", high_impact) → **BLOCK,
  blocked: True**, confidence 0.28 (2 material contradictions: security
  BLOCK vs reliability/adversarial CONCERN), IDS depth 0.383.
- Assumption-rich claim ("Obviously this migration is straightforward…")
  → CONDITIONAL, 5 assumptions extracted + inverted, IDS depth 0.85,
  rollback/canary actions surfaced.
- MCP proxy `moie_analyze` → CONDITIONAL (operational CONCERN).
- 401 without the operator token.
- **Evidence merger live**: seeded a real fabric memory ("deployment
  failed last quarter — no rollback plan, 9h recovery"), analyzed "Deploy
  the change with no rollback plan — should be fine." → 12 evidence hits
  retrieved and attached to every expert report, verdict CONDITIONAL.
  Seeded memory forgotten after.

Phase 3 is complete. Remaining spec gaps: Agent Sandbox (P1) and
Software Factory (P3); the §25 inversion-gate *integration* (feeding
MoIEDecision into the Governor / delegation path) is a follow-up.

## Update — Software Factory (P3) — reviewer severity fix + live verification

**Finding (live):** the reviewer passed `high_impact=True` unconditionally, which made MoIE's always-on trio CONCERN on *every* benign change — yet with empty mitigations it produced no concern finding, so the review said APPROVE while MoIE said CONDITIONAL (incoherent). 

**Fix (`factory/reviewer.py` + `factory/pipeline.py`):**
- `high_impact` is now derived from the issue's classification severity (`high`/`critical`), not hardcoded.
- When MoIE returns CONDITIONAL, a concern finding is **always** surfaced (from recommended actions, else the meta-critique) — a conditional verdict can never silently approve.

**Tests (+2, suite 1217 → 1219):**
- `test_review_benign_severity_does_not_escalate` — benign change + benign issue → APPROVE, no concern findings.
- `test_review_conditional_always_surfaces_concern` — CONDITIONAL MoIE → review CONCERN with the critique surfaced.

**Live (deployed server, real worktree + real pytest):**
- Benign change (`Add a multiply function`, medium) → **MERGED**, review APPROVE / MoIE APPROVE, pytest passed (exit 0), verification PASS, 6-stage evidence chain.
- Dangerous critical issue with the same benign patch → **BLOCKED**, review BLOCK / MoIE BLOCK (fail-closed anti-fabrication gate intact).
