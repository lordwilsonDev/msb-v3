# MSB-v3 System Architect / Repository Archaeologist

## Purpose

Use this skill whenever the task is to understand, organize, review, extend, or hand off the MSB-v3 repository.

The job is architecture reconstruction before modification.

Reconstruct:

surface -> canonical path -> authority -> execution -> evidence -> persistence -> operations

Then reconstruct why each important decision exists from:
1. current main code,
2. tests and adversarial tests,
3. dated ADRs / blueprints / release declarations,
4. commit messages and diffs,
5. observed operational evidence,
6. explicit supersession notes.

The model may propose an interpretation. Repository evidence decides.

## Core thesis

MSB-v3 is a single-operator, local-first, governed runtime.

Canonical execution:

request -> intent -> task DAG -> capability/authorization gate -> governed tools
-> verification -> evidence receipt/spine -> append-only audit chain/ledger -> replay

Authority ladder:

MODEL proposes
HARNESS constrains execution
VERIFIER evaluates evidence
MSB-v3 enforces deterministic policy
HUMAN resolves authority-boundary decisions

Never collapse these roles.

## Evidence vocabulary

Classify claims as:
- CLAIM: stated but not verified.
- DOCUMENTED: requirement/spec/ADR/blueprint.
- IMPLEMENTED: code path exists.
- TEST: automated test covers it.
- ADVERSARIAL: failure, bypass, tamper, race, or attack test.
- BENCHMARK: measured quantitative evidence.
- OBSERVED: real operation/run.
- LONGITUDINAL: observed repeatedly over time.
- STALE: dated snapshot or explicitly superseded statement.
- PROPOSED: design text explicitly says it is not built.
- UNKNOWN: evidence is insufficient.

A green test proves its assertion, not the whole system.

## System map

### Authority / governance
- governance/: capability registry/resolver, approvals, budgets, kill switch.
- agent/safety.py: ActionGate + SafeProvider; capability and taint boundary.
- gateway/: auditable compute/authorization routing.
- vesta/: signed-device approval perimeter.
- api/auth.py: operator authority.

### Execution
- agent/: intent, planner, DAG, executor, providers, trace.
- tools/: governed tool registry and runtime execution.
- local_ai/: local model backends.
- harnesses/: reusable harness scaffolding.
- conversation/: conversation contract and E2E path.
- tasks/: task lifecycle and observations.

### Intelligence / planning
- moie/: inversion/risk pre-filter, not security boundary.
- plei/: project lifecycle intelligence, provider selection, calibration.
- factory/: classify -> plan -> build -> test -> review -> verify -> merge.
- triumvirate/: Guardian / Argus / Hippocampus coordination vocabulary.
- flywheel/: research -> build loop behind governance brakes.
- wrongness/: deterministic falsification / contradiction support.

### Evidence / truth
- evidence/: decision-level spine and receipts.
- uac/: audit chain / anchoring.
- msb_ledger/: extracted standalone ledger.
- replay/: event reconstruction.
- observability/: metrics and audit views.
- vesta/evidence.py: content-addressed evidence blobs.

### Memory / persistence
SQLite stores, memory/, memory_fabric/, retrieval/, Qdrant, and vault helpers.

Never call all memory one thing. Separate runtime, persistent, evidence, and research memory.

### Operations
- cron/: scheduled governed jobs.
- wake/: resident wake loop.
- ops/: backup, restore, repair, discrepancy.
- runtime/: supervision/runtime state.
- scripts/launchd/: supervised jobs.

### Peripheral / experimental
Examples: speech/, node/, device/, integrations/, meta/, energy_matrix/, and optional research surfaces.
Classify them from docs/SURFACE.md. Existence does not make a subsystem canonical.

## Decision archaeology

For each major design choice, reconstruct:

problem / failure
-> decision or blueprint
-> implementation
-> test / adversarial evidence
-> observed outcome
-> current status
-> supersession / reversal condition

Important examples:

### UNKNOWN is not SAFE
An unregistered capability once inherited a safe tier. Phase 0 introduced explicit UNKNOWN semantics and conservative non-execution.

### Capability is the authority unit
Resolvers, keywords, and model confidence may produce evidence, but the gate authorizes.

### Evidence is its own substrate
The Evidence Spine, UAC, standalone ledger, Merkle receipts, anchors, and replay exist because normal logs do not provide the same provenance properties.

### Local-first is deliberate
The remote DeepSeek/frontier seam was retired. Oversized calls degrade to local execution and record that degradation instead of silently crossing the compute boundary.

### Architecture theater is rejected
The previous meta-cognitive planner was found to emit static ceremony rather than goal-dependent planning. It was flattened; real planning lives in agent.planner.

### Failure visibility matters
The repository repeatedly converts silent failures into visible failed/degraded states and adds regression tests.

## Review questions

### What I understand
- Which surface is changing?
- Is it canonical, load-bearing, frozen, optional, or proposed?
- Who is allowed to authorize it?
- What depends on it?
- What evidence proves it?

### Strong
- Which claims have adversarial or observed evidence?
- Which simplifications reduced coupling?
- Which boundaries are actually pinned by tests?

### Missing
- Which behaviors are only documented?
- Which contract fields are placeholders?
- Which docs are stale?
- Which external dependencies are required?
- Which failure modes are untested?

### Wrong / drift
Look for stale provider/model names, stale counts, claims with missing evidence,
unmapped packages, bypasses around governed execution, silent fallback, proposed text
treated as implementation, optional systems treated as canonical, and tests that pass
for the wrong reason.

### Control is not guarantee
Worktree isolation constrains an intended workspace; it is not a sandbox.
A test pass is not proof of production behavior.
A resolver confidence is not authorization.
A replay is not a rerun of the original action.

## Required workflow

1. Run: python scripts/msb-system-harness.py --check --markdown
2. Read docs/system-map.md, docs/decision-ledger.md, and docs/SURFACE.md.
3. Trace the canonical path beginning at agent/handle.py.
4. Trace authority through agent/safety.py, governance/, gateway/, and tools/runtime.py.
5. Trace evidence through evidence/, uac/, msb_ledger/, and replay/.
6. Trace persistence and operations.
7. Inspect relevant ADRs and recent commits.
8. Run targeted tests that can settle the question.
9. Record UNKNOWN rather than inventing a missing fact.

## Output contract

A completed architecture pass should produce:
- system map,
- canonical dependency / authority map,
- decision ledger,
- drift report,
- evidence status,
- open gaps,
- smallest next falsifiable validation.

## Non-goals

Do not create a second orchestrator beside the governed loop.
Do not promote MoIE into the security boundary.
Do not treat PLEI forecasts as ground truth.
Do not hard-code vendor-to-task routing.
Do not give a harness merge authority.
Do not treat dated snapshots as current without checking the tree.
Do not silently repair contradictions without recording the reason.
