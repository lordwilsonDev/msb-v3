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
| 2 | Evidence Spine (universal `event_id`, `DecisionEvidence`, causal links, evidence integrity) | ✅ done — 2.1 ✅ (`DecisionEvidence` + hash-chained `DecisionEvidenceStore`, `content_hash`/`parent_hash`/`audit_seq`); 2.2 ✅ (execution/result/verification vertebrae with `kind` + `parent_decision_id`, wired into the Vesta decision path); 2.3 ✅ (agent `handle()` slice wired end-to-end — local DAG path emits decision→execution→verification, delegated worker path emits the MoIE-gate decision→execution→verification — all through the shared `container.spine`) |
| 3 | State replay + crash recovery (`ReplayEngine`, event-sourced state) | in progress — 3.1 ✅ (`ReplayEngine` derives task state from the event log, validates transitions, detects projection divergence + illegal transitions, joins the spine decision trail; `reconcile()` reports divergences + in-flight work; exposed at `GET /agent/tasks/{id}/replay` via `container.replay`); 3.2 (kill-at-every-point crash-recovery acceptance, building on the stores' `recover_incomplete()`) → Phase 30 demonstration |
| 4 | Prove the existing tool loop (permitted / unauthorized / approval / killswitch / malformed) | ✅ done — hermetic matrix `tests/tools/test_governed_tool_loop.py` proves all five cases through `_run_governed` + `register_governed_tools`; `_run_governed` now honors `approval_required` (fail-closed) and audits every outcome (deny / approval-required / tool-error / success) so refusals leave evidence; killswitch proven at the `ActionGate`; the live Ollama hop stays opt-in (`MSB_LIVE_TESTS=1`) |
| 5 | Frontier seam (live smoke + provider-failure fallback) | ✅ done — hermetic `test_plan_degrades_to_template_when_frontier_fails` (provider 503 → template DAG, never uncontrolled execution) + opt-in `tests/live/test_frontier_smoke.py` (live hop: `resolve_client` → `FrontierClient` → `plan`, `MSB_LIVE_TESTS=1` + `OPENAI_API_KEY`); routing / async / FrontierClient-failure contracts already pinned in `tests/fabric/test_model_router.py` |
| 6 | Constitutional agent identity (agents cannot self-modify / self-grant / change policy) | partial (identity exists) |
| 7 | Multi-agent orchestration — deterministic DAG, 5 roles (Researcher/Analyst/Builder/Critic/Verifier) | not started |
| 8 | Agent Supervisor (assign/pause/resume/retry/cancel/quarantine/escalate) — itself governed | not started |
| 9 | Sandbox (READ_ONLY/LOW_RISK/SANDBOXED_WRITE/APPROVAL_WRITE/PRIVILEGED + budgets) | not started |
| 10 | Full tenant isolation (tenant_id propagates through the whole spine) | partial (RAG scoped) |
| 11 | Provider-neutral ModelGateway (Ollama / llama.cpp / frontier) | partial (gateway exists, frontier unverified) |
| 12 | MoIE integration into the agent pipeline (cannot bypass governance) | partial — diverse LLM reviewer panel (`LLMExpert` + `ReviewPanel` + `build_diverse_reviewer_panel`): model-backed reviewers behind the `Expert` interface; builder ≠ reviewer + pairwise-distinct invariants enforced at construction; concurrent `MoIEController.aanalyze()`; wired into the Software Factory review stage (`areview`, `Review.reviewer_models` provenance) and `POST /factory/run` (`reviewer_models`). Governance (MoIE cannot bypass policy) still to do |
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

## Diverse LLM reviewer panel (builder ≠ reviewer)

The factory's reviewer was deterministic rule-based MoIE — independent by
construction (it never reads the builder's summary) but unable to *read* a
change. The new panel closes that gap without giving up the invariant:

- `LLMExpert` implements the same `Expert` interface; a panel of N
  distinct-model reviewers (security → correctness → maintainability lenses
  cycled) runs the same fail-closed meta-critic.
- `ReviewPanel` enforces **builder ≠ reviewer** and **pairwise-distinct
  reviewer models** at construction (`__post_init__`); `SoftwareFactory`
  re-checks at the point of use so a builder/panel mismatch can never
  self-review (verdict `BLOCKED`).
- Reviewers run **concurrently** via `MoIEController.aanalyze()`; each
  reviewer's model is recorded on `ExpertReport.model` → `Review.reviewer_models`,
  so the evidence chain carries *who* reviewed *with which model*.
- Fail-closed: an unreachable model or unparseable output is a CONCERN, so a
  panel whose models are down can never APPROVE.
- Default reviewer models come from `MSB_REVIEWER_MODELS` (comma-separated),
  else the configured local model; `POST /factory/run` accepts
  `reviewer_models` to drive it end-to-end.

## Audit-chain security hardening (trust boundary + defense in depth)

Forensic review of the anchor/notary/audit subsystem surfaced a 9-item gap
list. The **code-side defense-in-depth** items are now implemented (the trust
root itself — hardware-bound key, off-box WORM notary, RFC 3161 — needs
operator/hardware decisions, see below):

- **#4 append-only storage** — `BEFORE UPDATE`/`BEFORE DELETE` triggers on
  `audit_records` refuse raw mutation at the SQL layer; `repair()` is the only
  sanctioned mutator (drops + restores the triggers inside one transaction).
  Tamper simulations now defeat the trigger via `tamper()` — the way a
  knowledgeable attacker would.
- **#5 hardened `repair()`** — operator auth when `MSB_OPERATOR_TOKEN` is set
  (constant-time, fail-closed), refuses when the last notarized tip is absent
  from the live chain (whole-DB rollback), and forces re-anchor + re-notarize
  after a successful repair.
- **#7 canonicalization** — non-finite floats (NaN/Infinity) are rejected at
  append (they emit non-standard JSON). Full RFC 8785 (JCS) is deferred to a
  versioned migration: it changes every existing record hash + anchor.
- **#3 verify-before-trust** — `verify_trustworthy()` re-checks internal chain
  + external anchor; Vesta `approve_and_execute` (shell + write) refuses any
  execution on an untrustworthy ledger.
- **#6 device binding** — a signed approval's audit record carries the
  device's signature + signed-payload hash (`signed_proof`), so one extracted
  record is independently attributable.

- **#1 hardware signing backend scaffold** — `uac/signing.py` adds a
  `SigningBackend` seam so the anchor key can move off-box without touching
  anchor/notary code: `SoftwareEd25519Backend` (current default),
  `SoftwareEcdsaBackend` (P-256/P-384, proves the non-Ed25519 wire format),
  `SecureEnclaveBackend` (macOS Secure Enclave P-256), and `YubiKeyPivBackend`
  (PIV slot). The anchor record now carries `key_algorithm`, and
  `chain_anchor.ChainAnchor` dispatches verification on it, so Ed25519 and
  P-256 (hardware) anchors coexist. `MSB_CHAIN_ANCHOR_BACKEND` selects the
  backend; unprovisioned hardware backends **fail closed**
  (`SigningBackendUnavailable`) rather than degrade to unsigned. The P-256
  verify path is tested end-to-end with a software ECDSA key sharing the exact
  hardware wire format (uncompressed point + DER ECDSA).

**Still needs operator/hardware (not code):** the actual key *migration* for
#1 (provisioning a Secure Enclave / YubiKey key — the seam is built and
P-256-verified, but the hardware `sign()` glue is an operator completion step
behind an optional PyObjC / PKCS#11 dependency, and the on-box Ed25519 seed
remains the live default, which is the *documented* trust boundary), #2 truly
off-box append-only notary (rclone primitive exists but is mutable + same-box
creds), #9 RFC 3161 trusted timestamping. #8 Merkle proof-of-inclusion is a
documented (not hidden) limitation: the ledger is a hash chain, not a tree.

## The one rule above all

Don't measure completion by code volume. Measure by the independent-engineer
demonstration in the North Star. That is the point where the BlackSwanLabz
"Sovereign AI" claim becomes a demonstrable engineering artifact.

## Phase 0 baseline (as of freeze)

- version 0.2.3 · commit `03df97d` · tests 1224/4 · portability 1228/4 ·
  lint clean · hygiene 12/12 · CI green (msb-v3 CI, factory-gate, harness-gate)
- see `docs/releases/v0.2.3-baseline.md`
