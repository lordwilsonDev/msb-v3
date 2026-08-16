# MSB v3 — Full Completion Blueprint (frozen 2026-08-16)

Status: **active program**. This document freezes the build order so MSB v3 is
finished as an *evidence-driven engineering program*, not by adding mythology,
names, or disconnected subsystems.

## North Star invariant

> **No agent, model, tool, provider, or subsystem can independently acquire
> durable authority. Every consequential action passes through
> capability → policy → authorization → execution → verification → provenance.**

Completion is measured by one demonstration, not code volume:

> Can an independent engineer take a fresh machine, deploy MSB, give it a
> mission, observe it make decisions, verify every consequential action,
> deliberately attack it, kill it, restore it, reconstruct what happened, and
> demonstrate that it never acquired authority it wasn't granted?

## Build order (linear, gated — do not skip ahead)

| Phase | Goal | Status (2026-08-16) |
|---|---|---|
| 0 | Freeze baseline (release doc + virgin-checkout test) | ✅ done (`7a3090b`) |
| 1 | Eliminate architectural debt (version drift, dual vector stores, JSON-file state, module singletons → DI) | ✅ done — 1.1 ✅ · 1.2 ✅ · 1.3 ✅ · 1.4 ✅ (ApplicationContainer composition root; all module-level service singletons migrated — vesta/memory/graph/flywheel/event_bus/identity/conversation-stub/planner-memory) |
| 2 | Evidence Spine (universal `event_id`, `DecisionEvidence`, causal links, evidence integrity) | in progress — 2.1 ✅ (`DecisionEvidence` + hash-chained `DecisionEvidenceStore` with `content_hash`/`parent_hash`/`audit_seq` cross-link, wired into the Vesta decision path); 2.2/2.3 (execution/result/verification vertebrae, multi-object causal graph) next |
| 3 | State replay + crash recovery (`ReplayEngine`, event-sourced state) | not started |
| 4 | Prove the existing tool loop live (permitted / unauthorized / approval / killswitch / malformed) | not started |
| 5 | Frontier seam (live smoke + provider-failure fallback) | not started |
| 6 | Constitutional agent identity (agents cannot self-modify / self-grant / change policy) | partial (identity exists) |
| 7 | Multi-agent orchestration — deterministic DAG, 5 roles (Researcher/Analyst/Builder/Critic/Verifier) | not started |
| 8 | Agent Supervisor (assign/pause/resume/retry/cancel/quarantine/escalate) — itself governed | not started |
| 9 | Sandbox (READ_ONLY/LOW_RISK/SANDBOXED_WRITE/APPROVAL_WRITE/PRIVILEGED + budgets) | not started |
| 10 | Full tenant isolation (tenant_id propagates through the whole spine) | partial (RAG scoped) |
| 11 | Provider-neutral ModelGateway (Ollama / llama.cpp / frontier) | partial (gateway exists, frontier unverified) |
| 12 | MoIE integration into the agent pipeline (cannot bypass governance) | not started |
| 13 | AIL as a formal research primitive (axiom → inversion → evidence → surviving model) | partial (methodology only) |
| 14 | Evaluation system (accuracy/grounding/policy/tool/recovery/latency/calibration + benchmark classes) | partial (governance evals) |
| 15 | Chaos engineering (kill/corrupt/delay/disconnect injections + fail-closed/recovery/evidence measurements) | partial (hygiene h01-h10) |
| 16 | RecoveryManager (checkpoint/restore/rollback/resume/quarantine/reconcile; UNKNOWN STATE ⇒ HALT) | partial (vesta recovery) |
| 17 | Security red team (permanent adversarial suite; attack → failing test → patch → regression → gate) | partial (approval-bypass, tamper) |
| 18 | Multimodal: finish it (through the same perimeter) or delete it — not before core runtime is done | deferred |
| 19 | Sensor plane (Observation → normalization → confidence → evidence → model) | not started |
| 20 | Actuator plane (intervention → policy → approval → actuator → measure → verify) | not started |
| 21 | Scientific experiment engine (hypothesis/variables/controls/stopping/rollback/reproducibility) | not started |
| 22 | Formal safety model — invariants I1–I8 (no unauthorized mutation, no unverified durable state, no hidden escalation, no cross-tenant flow, no irreversible action w/o authorization, detectable audit integrity, unknown-state halt, model output ≠ authority) | partial (implicit) |
| 23 | Performance engineering (P50/P95/P99, memory, IO, contention) — after correctness | not started |
| 24 | Mac mini deployment hardening (ordered startup: hardware→network→storage→Ollama→Qdrant→MSB→health→governance→READY) | partial |
| 25 | Backup / DR (snapshot + event log + external anchor; destroy → restore → reconstruct → verify → resume) | partial (backup exists) |
| 26 | Distributed sovereign nodes (mesh; identity→capability→authorization→evidence, no blind trust) | not started |
| 27 | Node-to-node delegation (delegation as an auditable object) | not started |
| 28 | Sovereign Agent Factory (mission → spec/caps/risk/tools/evals/sandbox/identity/tests → build→test→red-team→review→register→deploy) | not started |
| 29 | Continuous evolution (Ouroboros loop; propose ≠ authorize) | not started |
| 30 | Final acceptance tests T1–T10 (autonomous research, governed mutation, unauthorized block, kill switch, provider failure, DB failure + replay, audit tamper detect, tenant attack, privilege escalation, full mission reconstruction) | not started |

## Maturity model (tracking)

L1 Local runtime ✅ · L2 Agent execution ✅ · L3 Governance perimeter ✅ ·
L4 Auditable execution ✅ · L5 Sovereign/local-first ✅ · L6 Durable
memory/RAG/context ✅ · L7 Multi-agent ❌ · L8 Replay/recovery ❌ · L9 Strong
sandbox ❌ · L10 Tenant isolation ⚠️ · L11 Provider-independent model fabric ⚠️ ·
L12 MoIE production loop ❌ · L13 Scientific-control substrate ❌ · L14
Distributed nodes ❌ · L15 Governed agent factory ❌ · L16 Continuous evolution ❌.

**Current position: ~L5–L6.** The failure mode to avoid is jumping to L13–L16
before L7–L12 are proven.

## The one rule above all

Don't measure completion by code volume. Measure by the independent-engineer
demonstration in the North Star. That is the point where the BlackSwanLabz
"Sovereign AI" claim becomes a demonstrable engineering artifact.

## Phase 0 baseline (as of freeze)

- version 0.2.3 · commit `03df97d` · tests 1224/4 · portability 1228/4 ·
  lint clean · hygiene 12/12 · CI green (msb-v3 CI, factory-gate, harness-gate)
- see `docs/releases/v0.2.3-baseline.md`
