# MSB v3 — Live-Loop Composition Plan (M2 priority)

**Owner:** Wilson · **Author:** Buffy · **Date:** 2026-08-16 · **Status:** proposed, pending approval
**Supersedes/extends:** [`MILESTONES.md`](MILESTONES.md) M2 + M4 · **Goal:** prove the built-in surfaces compose into **one reliable live loop** — the review's bottom line.

## 1. Verified starting state (checked 2026-08-16, not assumed)

| Review claim | Verified reality | Consequence |
|---|---|---|
| "Phrase-query failure" blocks semantic search | **STALE** — no `phrase` concept anywhere in `rag.py`; the MCP `search_query` tool does `/rag/search` (Qdrant + embeddings) **with substring fallback** (mcp_bridge.py:490-512) | Not a blocker. Don't plan around it. |
| "MCP surface is broad but maybe a thin proxy" | **PARTIALLY TRUE** — the MCP `chat` tool proxies to `/chat` (mcp_bridge.py:341), and `/chat` (chat.py:47) does **not** route through the governed `agent.handle()` loop. Only `/agent/handle` is operator-gated + ActionGate-governed | This is the real gap: the most-used MCP surface bypasses the governance spine. |
| Surfaces are real | MCP bridge (25+ tools, auth + audit on every call), vault read/write, memory fabric (store/recall/verify/forget/consolidate), context engine L0-L7, MoIE, factory, codegraph, ralph loop, backup/restore — all with tests | Composition is the only missing proof. |

**North-star invariant (unchanged):** every consequential action passes capability → policy → authorization → execution → verification → provenance. Today that's proven for `/agent/handle`; it is **not** proven for `/chat` or the MCP surface that fronts it.

## 2. The one sentence this plan proves

> A real request entering through any surface (MCP tool, chat, agent endpoint) traverses **governance → execution → verification → evidence**, and a denied or failed request cannot silently become an allowed action.

## 3. Build order — prioritized for speed, each step independently shippable

### P0 — M2: Governance in the Loop on the canonical path (the proof burden)
**Goal:** the ActionGate is on the *live* path with regression-proof bypass coverage and observable decision metrics.

1. **Bypass regression suite** (`tests/governance/test_bypass.py`):
   - Direct tool invocation (import the executor and call it without `handle()`) must fail closed.
   - Alternate HTTP callers (`/chat` with tool payloads, MCP `chat` tool) must not reach tools ungoverned.
   - Replay/retry of a denied request must re-evaluate, not cache an allow.
2. **Governance metrics** — extend `ROUTER_DECISIONS` (or add `ACTIONGATE_DECISIONS`) to count **allowed / denied / indeterminate / failed / stub** per gate; assert in tests.
3. **Denial→recovery flows as tests**: DENY → no execution + audit; REQUIRE_APPROVAL → pause → approve → execute; KILLSWITCH → block all mutation. (Tests exist for pieces; consolidate into the M2 suite with the metrics.)
4. **Exit evidence:** `tests/governance/` suite green; metrics histogram in `/metrics/prometheus`; M2 row in `MILESTONES.md` flips 🟢.

### P1 — Close the real MCP gap (the review's "thin proxy" concern)
**Goal:** the MCP surface fronts the governed loop, not a parallel ungoverned one.
- Route MCP `chat` through `handle()`-style governance when the request carries tools/consequential intent; plain conversational turns may stay on the fast path — but the decision must be **explicit and tested**, not implicit.
- Every MCP tool call already audits (`_log_audit`); add the gate verdict to the audit record so the chain answers "allowed/denied under which policy."
- **Exit evidence:** a test that drives MCP `chat` with a tool request and asserts the ActionGate verdict appears in the audit chain; a test that a denied tool call through MCP produces **no** side effect.

### P2 — Live-loop composition test (the review's ask, literally)
**Goal:** one opt-in integration test (`tests/live/test_live_loop.py`, `MSB_LIVE_TESTS=1` like `test_frontier_smoke.py`) that walks a real request through the whole spine against a running stack:
```
MCP call → auth → gate verdict → execute → verify → evidence append → audit verify → replay
```
covering: vault search (RAG), memory store+recall, context compose, MoIE analyze, factory run — **the five surfaces the review listed**, in one chain, with the evidence chain verified afterward.
- **Exit evidence:** the live test passes against the real stack (Ollama + Qdrant up); its run artifact is committed as a golden fixture.

### P3 — Vault-to-code intelligence loop (the review's #2 connection)
**Goal:** one workflow that starts in Obsidian and ends with a tested, verified change written back.
```
Vault task → memory recall → codegraph blast radius → propose patch → run tests → verify claims → write result to Vault
```
- Wire the existing pieces (vault write, codegraph impact, factory) into a single script/endpoint under `experiments/` first; promote only if it proves itself.
- **Exit evidence:** a recorded run of a real task (e.g. the daily-note/vault-search scenario) with the evidence chain intact.

### P4 — M4: Factory dogfood (one real change through the factory)
**Goal:** MSB builds, reviews (diverse-LLM reviewer, builder∉reviewers), verifies, and lands one real change on itself — with the seeded-defect proof.
- Reuse the existing `factory/` + `moie/llm_experts.py` reviewer panel; run a real MSB change (pick a small P2/P3 item) through it; record the artifact; catch one seeded defect.
- **Exit evidence:** run artifact linking request → generated change → review → verification → merge decision; defect-detection record.

### P5 — External MCP client smoke (optional, needs external tooling)
- One session of Claude/Cursor/Open WebUI pointed at the MCP bridge (auth secret in `.env`), executing a governed tool call and reading the audit record.
- **Exit evidence:** a documented, reproducible 5-minute setup + one successful governed call. (Do only if P0-P2 are green — this is a demo, not a dependency.)

## 4. Explicitly deferred (parked, not forgotten)
- New modalities, more agent personas, more providers, dashboards, distributed nodes — all post-M7 per the v3 contract. No new subsystem enters during P0-P2 unless a core-path failure demands it (Rule 3).

## 5. Definition of done (the review's finish line, made testable)
1. A request through **any** surface (MCP, chat, agent) that attempts a consequential action is governed — denied actions produce no side effect and an audit record.
2. `tests/governance/` + `tests/live/test_live_loop.py` green (live test against the real stack, artifact committed).
3. One real MSB change lands via the factory with a diverse reviewer and seeded-defect evidence.
4. `MILESTONES.md`: M2 🟢, M4 🟢, ledger rows updated with evidence links.

## 6. Risk / unknowns
- `/chat` carries `tools` in its payload today (chat.py:51-54) — the fast path already accepts them; P1 must decide **explicitly** whether they're governed (they should be) without breaking existing chat tests. This is the highest-risk change; the bypass suite (P0) must land first.
- Live-loop test depends on Ollama/Qdrant being up — same opt-in pattern as `test_frontier_smoke.py`, so CI stays green without them.

## 7. Immediate next action (this session)
1. Write the P0 bypass suite + metrics.
2. Land P1's MCP-governance decision + audit-verdict recording.
3. Then P2 live-loop test.
