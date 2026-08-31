# MSB v3 — Production-Readiness Roadmap

**Blueprint:** PRODUCTION-CLOSURE-001 · **Baseline:** v0.4.2 (`7a1ceb9`)
**Definition of production-ready:** every production-claimed capability is
connected to the canonical path, protected by the authority boundary,
exercised under failure, independently verified, observable, recoverable, and
backed by evidence. The production *boundary* must be complete; experimental
surfaces (Meta-System META-1..8, speech, energy, graph, EAAE) do **not** block.

This document is the executable backlog. Each item: what "done" means, the
concrete change, acceptance, and rough effort. Items run top-to-bottom;
within a phase, sub-items are ordered.

---

## Status legend

`DONE` verified + evidence · `SCOPED` plan exists, not started · `OPEN` not scoped

---

## ✅ Complete (v0.4.2)

| Phase | Item | Evidence |
|---|---|---|
| P0 | Baseline lock | `docs/releases/PRODUCTION-BASELINE.md` |
| P1 / O1 | Hermetic `release-verify` | green virgin-clone on v0.4.2 (3040 passed) |
| P2 / O2 | Release truth — version identity + CI-verified tag | `docs/releases/v0.4.2.md` |

---

## P3 / O3 — Authority closure  ·  SCOPED  ·  ~2–3 sessions

**Blocker:** the gateway is a best-effort audit ping on `agent/handle.py`,
not an enforced doorway. `test_gateway_canonical.py` accepts the audit-only
model. Full plan + 14-path matrix: `docs/releases/O3-AUTHORITY-CLOSURE-PLAN.md`.

**Decision required first (Wilson):**
- **Option A** — make the gateway the enforced doorway (raises on deny, real
  capabilities, no `try/except` swallow); tighten the test.
- **Option B** — formally accept dual-governance (gateway = audit, ActionGate
  = enforcement); the criterion becomes "every entry path reaches ActionGate";
  amend the blueprint criterion with rationale. *Lower risk, matches the code.*

**Work (after the decision):**
1. Map all 14 entry paths (agent/handle, chat, mcp_bridge, cron, wake, hook,
   automation, factory, flywheel, direct provider, replay, internal
   tool-registry import, background task, `/v1`) from entry to first
   capability/tool execution.
2. Close every path that reaches a capability without crossing authority.
3. New `tests/architecture/test_authority_boundary.py` — one adversarial case
   per path, asserting ALLOW-through-authority or DENY, never a third state.
   Make it a **blocking** CI job.
4. Fill the matrix; zero `UNKNOWN` is the bar.

**Acceptance:** decision written; 14/14 paths ALLOW-or-DENY; adversarial suite
green and blocking in CI; `test_gateway_canonical.py` no longer accepts an
un-mediated path.

---

## P4 — Provider interchangeability proven  ·  SCOPED  ·  ~1–2 sessions

`ProviderContract v1` (`agent/contract.py`) + conformance suite exist (190
tests). 10 providers registered (`local.slice`, `api.anthropic`,
`api.deepseek`, `dsh.headless`, `cli.{claude,codex,opencode}`,
`paseo.{claude,codex,opencode}`). Not yet proven: identical governed workflow
across a real swap under failure.

**Work:**
1. `tests/contracts/test_provider_failure_matrix.py` — for each *reachable*
   provider (Ollama + Claude now; DeepSeek gated on C1): normal request,
   structured response, tool request, timeout, network failure, malformed
   output, rate limit, auth failure, model-unavailable, fallback, recovery.
   Record provider / model / latency / tokens / cost / failure / fallback /
   verification per case.
2. Interchange proof: run one governed workflow end-to-end with
   `resolve_client` pinned to Ollama, then Claude, then (when unblocked)
   DeepSeek — assert the governance semantics and canonical lifecycle are
   byte-identical, only the implementation differs.
3. Wire the matrix into CI (Ollama arm always; cloud arms behind a secret).

**Acceptance:** provider replacement requires zero change to governance or the
canonical lifecycle; failure matrix green for every reachable provider;
interchange proof passes for ≥2 providers.

**Blocked-partial:** DeepSeek arm needs **C1** (refill credits →
`curl https://api.deepseek.com/v1/models` = 200). Consider OpenRouter as a
single-key multi-provider path.

---

## P5 — Model routing measurable  ·  OPEN  ·  ~1 session

`plei/decisions/provider_selection.py::select_provider_for_task` exists but
routing is not benchmarked or observable.

**Work:**
1. Every routing decision emits a structured event: request class → policy →
   provider selected → outcome, keyed to the request's `execution_id`.
2. Routing dimensions explicit: privacy, complexity, latency, cost,
   availability, context size, local/cloud preference.
3. `tests/routing/test_routing_benchmark.py` — a fixed task set, measure
   accuracy / latency / cost / failure rate / fallback rate per route; assert
   decisions are deterministic for fixed inputs.

**Acceptance:** routing decisions are observable end-to-end and benchmarked
against a task set with recorded metrics.

---

## Hardening — Wave 2 (P6–P20)  ·  OPEN  ·  ~4–8 sessions total

These do **not** block a "production-ready canonical runtime" tag but are
required for "production-ready *product*". Ordered by dependency, not number.

| # | Phase | One-line "done" | Effort |
|---|---|---|---|
| H1 | P12 Observability | Every run has an `execution_id` linking request → plan → auth → tool calls → provider calls → metrics → verification → evidence → audit → result. Prereq for verifying H2–H6. | ~1 sess |
| H2 | P16 Performance baseline | p50/p95/p99 + throughput + resource use for the 8 representative workloads, recorded. | ~1 sess |
| H3 | P13 Security hardening | Threat model (prompt/tool injection, path traversal, SSRF, cred theft, webhook spoof, replay, audit tamper, resource exhaustion) each has a test proving "LLM compromise ≠ host compromise". | ~2 sess |
| H4 | P14 Secrets | LLM never receives raw credentials; capability-reference → broker → secret → external API. Exposure tested through prompt/tool-output/logs/errors/memory/audit/RAG. | ~1 sess |
| H5 | P9 Automation safety | PLAN → PREVIEW → AUTHORIZE → EXECUTE → VERIFY → RECORD with idempotency keys, rate limits, budgets, dedup, external op IDs. Test: one malformed request cannot produce uncontrolled repeated external actions. | ~1 sess |
| H6 | P10 Cron/wake reliability | Job lease + heartbeat + idempotency; kill-runtime-mid-job → restart → recover without double execution. | ~1 sess |
| H7 | P11 Backup/restore | LIVE STATE → BACKUP → DESTROY → RESTORE → VERIFY across SQLite / config / audit ledger / evidence / Qdrant / memory / operator state. RPO + RTO defined and demonstrated. | ~1 sess |
| H8 | P8 Memory/Storage authority | One documented model: ephemeral / session / project / operator / system / audit — each with owner / schema / retention / access / deletion / backup / consistency / authority. Survives restart + restore. | ~1 sess |
| H9 | P18 DB migration | Every schema change: backup → migrate → verify → startup → rollback path. Stamp the 26 SQLite DBs currently at `user_version=0`. | ~1 sess |
| H10 | P17 Supply chain | Locked dependencies + SBOM + vuln scan + license review + reproducible install. Build fails on critical integrity regression. | ~1 sess |
| H11 | P15 Resource governance | Mac-mini operating envelope measured (CPU/RAM/unified-mem/disk/Qdrant/SQLite/net/concurrency); hard limits; degrades predictably under 8 pressure scenarios. | ~1 sess |
| H12 | P6 Local data analysis | Model emits a constrained plan (JSON), MSB validates, local executor (DuckDB primary, Pandas secondary) runs it — model gets no filesystem exec. Deterministic result matches independent ground truth on a 1M-row set + 12 adversarial inputs. **New feature — first post-boundary workload.** | ~2 sess |
| H13 | P19 Release/rollback | BUILD → TEST → SECURITY → PACKAGE → BACKUP → DEPLOY → SMOKE → HEALTH → OBSERVE, with DETECT → STOP → ROLLBACK → VERIFY → RECORD on failure. | ~1 sess |
| H14 | P20 Operator runbook | `docs/ops/` — INSTALL, START, STOP, HEALTH, BACKUP, RESTORE, ROLLBACK, INCIDENT, PROVIDERS, SECRETS, SECURITY, RELEASE. Another competent operator runs the system with no tribal knowledge. | ~1 sess |
| H15 | P24 Acceptance matrix | Fill the 21-row production acceptance matrix; every row has evidence; zero `UNKNOWN`. | ~0.5 sess |

---

## Also-open (from the closer audit, not in the 20 phases)

- **O4** — `codegraph` index is empty live (`codegraph_stats` = 0). Populate +
  verify, or downgrade the "V8 resolved" claim. ~0.5 sess.
- **Debt** — `MemoryStore` → `memory_fabric.store` migration (DeprecationWarning
  every request); `ruff format` the ~516-file drift or add `--check` to CI.
- **O2 residue** — no *machine*-enforced "tag only on green" (the
  `release-tag-immutability` ruleset can't require status checks on tag
  creation — see `CLAUDE.archive.md` → Tag ruleset). Process-enforced today.

---

## Definition of Done (per capability, from the blueprint §28)

`connected + authorized + exercised + failure-tested + independently verified
+ observable + recoverable + documented`. Only `CLOSED` counts. `BUILT` ≠
`INTEGRATED` ≠ `VERIFIED` ≠ `CLOSED`.

## Sequence

```
P3 (decision → close)  →  P4  →  P5     ← production-ready canonical RUNTIME
        │
        ▼
H1 (execution_id)  →  H2  H3  H4  H5  H6  H7  H8  H9  H10  H11
        │
        ▼
H12 (local data)  →  H13  H14  H15       ← production-ready PRODUCT
```
