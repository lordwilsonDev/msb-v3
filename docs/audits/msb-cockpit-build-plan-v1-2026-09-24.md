# MSB Cockpit Build Plan v1.0

**Status:** Draft for execution  
**Date:** 2026-09-24  
**Scope:** Full implementation of the Cockpit build-verification interrogation set  
**Plan type:** Spec-first, evidence-gated, phased implementation  

> This is a plan, not a claim of completion. The plan deliberately separates code that can be implemented and tested locally from guarantees that require a second Apple Silicon host, external storage, controlled power loss, and independent verification.

## 1. Outcome

Build a production-capable MSB Cockpit for a Mac mini M1 (2020) whose critical guarantees are:

- mechanically enforced where practical;
- observable from outside the Cockpit;
- recoverable after power loss or internal SSD replacement;
- independently verifiable on a replacement Apple Silicon host;
- never represented as stronger than the evidence permits.

The target authority chain is:

```text
ACTION PRODUCER
  → AUTHORITY
  → EXECUTOR
  → INDEPENDENT VERIFIER
  → EVIDENCE
  → AUDIT
  → EXTERNAL CHECKPOINT
```

Observation is not execution. Verification is not execution. Internal consistency is not external authenticity.

## 2. Current Repository Baseline

The following foundations already exist and must be extended rather than replaced:

- `src/msb_ledger/audit_chain.py` — SQLite hash chain, sequence allocation, append-only triggers, concurrent append locking, chain verification.
- `src/msb_ledger/chain_anchor.py` — signed chain anchors and fail-closed protection against unanchored production appends.
- `src/msb_ledger/notary.py` — local append-only notarization plus optional per-object remote sinks and remote-head verification.
- `src/msb_ledger/merkle.py` — Merkle root and inclusion-proof primitives.
- `src/msb_v3/infrastructure/qdrant_contract.py` — Qdrant preflight contract.
- Existing `launchd` LaunchAgents and operational scripts.
- Existing governance, approval, evidence, replay, task lifecycle, and health surfaces.

Known baseline gaps to address:

- SQLite durability/WAL and storage-state enforcement are not yet proven as one unified contract.
- The external trust-anchor path exists but needs explicit restore acceptance and replacement-host verification.
- Power-loss, disk-full, Qdrant restart, macOS migration, and second-host restore paths need dedicated harnesses.
- Cockpit resource budgets and admission control need to be made first-class and measurable.
- The 104 gates need a machine-readable verification matrix rather than prose claims alone.
- A complete Cockpit UI/API and operator authority separation must be built around the verified data plane.

## 3. Non-negotiable Constraints

### Physical constraints

- Mac mini M1, 2020, model identifiers `Macmini9,1` / `A2348` are the target baseline.
- 16 GB unified memory is the production minimum.
- 8 GB configurations are degraded-mode targets, not silent-swap production targets.
- The internal SSD and memory are not user-upgradeable.
- The Mac mini is one complete physical failure domain.
- CPU, GPU, Neural Engine, SQLite page cache, Qdrant, and model weights share the same memory pool.
- External Thunderbolt/WORM-capable storage is a first-class trust-anchor domain.

### Evidence constraints

- No claim of strong audit integrity or ten-year recoverability before mechanical controls, external checkpoints, restore testing, and independent verification exist.
- A passing unit test is implementation evidence, not physical-world proof.
- A second user/process is a fallback isolation boundary; a second Apple Silicon machine is preferred for final trust verification.
- A backup is a hypothesis until it has been restored and verified.
- A live Qdrant service is not healthy until an independent vector-store integrity check passes after restart.

### Scope boundaries

This plan does not assume:

- upgradeable RAM;
- replaceable internal SSDs;
- multi-socket memory;
- infinite disk;
- configuration convention as a security boundary;
- a Cockpit observing itself as its only observer;
- a code-level hash chain as proof of external authenticity by itself.

## 4. Priority Model

### P0 — Safety-critical foundations

1. Canonical audit schema and independent chain verifier.
2. Mechanical append-only semantics and SQLite durability contract.
3. External signed checkpoint/notary path.
4. Restore acceptance against an external anchor.
5. Storage state machine and fail-closed audit writes.
6. Physical test harness for power loss, disk-full, deletion, rollback, and replacement-host restore.
7. Independent verification boundary and evidence records.

### P1 — Operational Cockpit

1. Resource telemetry and bounded executor budgets.
2. Admission control and degraded mode.
3. Operator authority separation and emergency controls.
4. Cockpit API and dashboard over the verified data plane.
5. Qdrant restart integrity checks and Apple Silicon/container constraints.
6. `launchd` supervision, health-gated restart, backoff, and safe mode.

### P2 — Durability and migration hardening

1. Versioned schema migrations preserving pre- and post-migration heads.
2. Segment rotation and archival with chain continuity.
3. Environment manifests attached to checkpoints.
4. macOS major-upgrade and replacement-machine procedures.
5. Retention, decay, quarantine, and archival policy.
6. Long-term evidence durability and 2036-oriented packaging.

### P3 — Optimization and expansion

1. Performance tuning after correctness gates pass.
2. Additional observability panels and self-probes.
3. Broader replay and adversarial coverage.
4. Optional second-host automation and operator ergonomics.
5. Documentation, training, and release packaging.

## 5. Phase Plan

## Phase 0 — Freeze Scope and Build the Verification Matrix

**Intent:** Convert the 104 questions into executable gates before implementation begins.

### Work

- Create a machine-readable gate manifest with one record per interrogation gate.
- Add fields: `id`, `priority`, `requirement`, `mechanical_enforcement`, `artifact`, `test`, `environment`, `evidence_status`, `owner`, `last_verified_at`, and `blocking_dependencies`.
- Classify each gate as `confirmed`, `inferred`, `hypothesis`, `unknown`, or `not_yet_implemented`.
- Mark gates requiring physical hardware or a second host.
- Trace every planned code change to one or more gate IDs.

### Artifacts

- `config/cockpit_verification_manifest.yaml`
- `docs/audits/msb-cockpit-verification-matrix-v1.md`
- Initial test-to-gate traceability report.

### Exit gate

- Every gate has an ID, status, owner, and evidence location.
- No gate may be marked `PASS` without a machine-run artifact or a clearly labelled human verification record.
- The plan is approved as an execution baseline.

**Reversibility:** Fully reversible; documentation and manifest only.

---

## Phase 1 — Canonical Audit Record and Independent Verifier

**Intent:** Make one committed audit record deterministic, verifiable without producer code, and resistant to accidental interpretation drift.

### Requirements

- Introduce an explicit `schema_version`.
- Define canonical serialization rules and reject non-finite numeric values.
- Include record type, component, actor, action, timestamp, payload, sequence, predecessor hash, record hash, and environment reference.
- Define monotonic sequence semantics independent of SQLite row IDs where possible.
- Detect gaps, reordering, substitution, truncation, and malformed records.
- Provide a standalone verifier that uses only the documented schema and cryptographic algorithm.
- Preserve existing historical records through a versioned migration; never silently rewrite old hashes.

### Implementation areas

- `src/msb_ledger/audit_chain.py`
- `src/msb_ledger/canonical.py` if a shared pure-function module is justified.
- `src/msb_ledger/verifier.py` or a narrowly scoped standalone verifier module.
- `tests/ledger/test_audit_chain*.py`

### Verification

- Golden canonicalization vectors.
- Cross-language-compatible canonical JSON vectors.
- Tamper matrix for every field.
- Gap and reordering tests.
- Historical-chain migration test.
- Independent verifier run from a clean test directory with no producer imports.

### Exit artifacts

- Canonical schema specification.
- Standalone verifier CLI.
- Golden vectors.
- Green unit and adversarial tests.
- Gate mappings for questions 19–24 and 51–52.

**Reversibility:** Reversible before production use; historical migration must be backed up and tested.

---

## Phase 2 — Mechanical Storage and SQLite Durability Contract

**Intent:** Make committed audit data resistant to deletion, update, truncation, power-loss ambiguity, corruption, and disk exhaustion at the storage layer.

### Requirements

- Use a documented SQLite journal mode, synchronous mode, busy timeout, foreign-key behavior, and checkpoint policy.
- Verify settings on every connection or through a single shared connection factory.
- Keep append-only triggers and test both ordinary and privileged mutation paths.
- Detect database and WAL corruption independently.
- Define behavior for:
  - power loss;
  - WAL recovery;
  - disk-full writes;
  - page corruption;
  - concurrent copies;
  - schema migration interruption;
  - SQLite version changes.
- Add bounded write queues and audit write rate limits suitable for a fixed-capacity SSD.
- Make all storage state transitions explicit and observable.

### Required state machine

```text
NORMAL
  → LOW_SPACE
  → CRITICAL_SPACE
  → AUDIT_WRITE_BLOCKED
  → SAFE_MODE
```

Rules:

- `LOW_SPACE` triggers archival pressure and operator warning.
- `CRITICAL_SPACE` stops nonessential writes and prepares a checkpoint.
- `AUDIT_WRITE_BLOCKED` refuses new authoritative records rather than silently dropping them.
- `SAFE_MODE` exposes recovery and repair actions only.
- The state transition itself must be recorded where possible; if storage cannot accept the record, the refusal must be externally observable.

### Tests

- WAL and synchronous behavior under subprocess termination.
- `ENOSPC` simulation through a bounded test filesystem or injected storage failure boundary.
- Corruption and truncated-record cases.
- Concurrent readers and writers.
- Migration interruption and restart.
- Storage-state transition table tests.
- SSD write-rate and queue-depth tests.

### Exit gate

- A failure cannot produce a healthy-looking state.
- A storage refusal cannot be mistaken for a successful append.
- The full storage contract is documented and automatically verified.

**Reversibility:** Database settings are reversible; production migration requires backup and restore rehearsal.

---

## Phase 3 — External Trust Anchor, Checkpoint, and Notary

**Intent:** Detect same-box deletion, rollback, snapshot replacement, and attacker-controlled local rewrites.

### Requirements

- Every checkpoint contains sequence, head hash, schema version, environment manifest, and checkpoint signature.
- Checkpoints are copied as immutable-per-object values to an independently controlled sink.
- The verifier reads the remote checkpoint head, not merely the local last line.
- Remote absence, local rollback, remote rollback, signature failure, sequence gap, and stale checkpoint are distinct verdicts.
- Local-only mode is explicitly labelled as insufficient for the ten-year property.
- Rotation preserves predecessor hashes and checkpoint continuity.
- The external trust-anchor medium is rotated on a documented schedule.

### Implementation areas

- `src/msb_ledger/chain_anchor.py`
- `src/msb_ledger/notary.py`
- `src/msb_ledger/checkpoint.py`
- `src/msb_ledger/environment_manifest.py`
- `scripts/notarize_chain_anchor.sh`
- `scripts/verify_chain_anchor.sh`
- `config/notary.example.env`

### Verification

- Local deletion and rollback.
- Remote deletion and replacement.
- Signature substitution.
- Sequence gap and reordering.
- Stale but valid checkpoint behavior.
- Power loss between local append and remote push.
- Independent remote-head verification.
- WORM/object-lock configuration check where supported.

### Exit gate

- Whole-DB replacement is detected when an external checkpoint exists.
- A remote push failure is loud and cannot report success.
- A local-only checkpoint is visibly not equivalent to an off-box checkpoint.

**Reversibility:** Checkpoint publication is additive; remote deletion or credential rotation is externally consequential and requires approval.

---

## Phase 4 — Restore, Migration, and Replacement-Apple-Silicon Recovery

**Intent:** Make recovery an exercised operation, not an imagined one.

### Recovery classes

1. Power loss with intact SSD.
2. Power loss with WAL recovery.
3. Disk-full or corrupt live store.
4. Accidental directory deletion.
5. Older backup rollback.
6. Internal SSD failure.
7. Logic-board or SoC failure.
8. FileVault key loss.
9. macOS major-version upgrade.
10. Replacement Apple Silicon host.

### Restore contract

A restore is accepted only when:

- the external checkpoint is reachable;
- the checkpoint signature verifies;
- the restored chain reaches a valid predecessor/head relationship;
- the entire available chain is verified;
- no unexplained sequence gaps exist;
- environment manifest and schema version are recorded;
- the restored store is quarantined until verification completes;
- the operator explicitly promotes it from quarantine to authoritative state.

### Mac replacement procedure

1. Acquire a replacement Apple Silicon host with equal or greater capability.
2. Install a pinned supported macOS/runtime baseline.
3. Install the verified application release and dependencies.
4. Restore the external checkpoint and signed segments.
5. Verify the chain independently before opening write paths.
6. Reconstruct required state stores from signed evidence and verified projections.
7. Run Qdrant and vector integrity checks.
8. Run the full adversarial test matrix.
9. Issue a new environment manifest and checkpoint.
10. Only then declare the replacement host operational.

### Artifacts

- Restore runbook.
- Machine-readable restore checklist.
- Backup catalog and retention schedule.
- Replacement-host dry-run report.
- Restore result schema.
- Recovery evidence bundle.

### Exit gate

No physical failure class is marked recovered until at least one controlled test produces a complete evidence bundle.

**Reversibility:** Restore is high-impact. All restore operations default to quarantine and read-only verification.

---

## Phase 5 — Resource Containment and Admission Control

**Intent:** Make the 4P+4E CPU topology and 16 GB unified memory a first-class execution constraint.

### Requirements

- Explicit executor saturation limits.
- Per-panel and per-request budgets.
- Bounded queue depth.
- Admission control before model, SQLite, Qdrant, and filesystem work.
- Hard resource budget for self-probes and observability.
- Memory-pressure and swap telemetry.
- Degraded mode when thresholds are crossed.
- 8 GB compatibility mode with no silent swap thrashing.
- Qdrant and model workloads cannot starve audit writes.
- The Cockpit is not the sole observer of the Cockpit.

### Proposed resource state model

```text
NOMINAL
  → PRESSURE
  → DEGRADED
  → ADMISSION_CLOSED
  → SAFE_MODE
```

Each transition records cause, measurements, affected capabilities, and recovery condition.

### Verification

- 4P+4E sustained load.
- 8 GB simulation and physical 8 GB test where available.
- Concurrent SQLite + Qdrant + model workload.
- Self-probe starvation test.
- Queue saturation and recovery test.
- Swap-pressure threshold test.
- No silent event-loop starvation.

### Exit gate

The system has measurable limits and refuses work before the critical path becomes unresponsive.

**Reversibility:** Configuration changes are reversible; safe mode entry is intentionally conservative.

---

## Phase 6 — Authority, Operator, and Emergency Control Plane

**Intent:** Separate action production, authorization, execution, verification, and audit.

### Requirements

- `localhost` is insufficient for authorization.
- Operator identity is authenticated independently of the request path.
- Action producers cannot grant themselves authority.
- Executors cannot verify their own results.
- Audit failures fail closed for consequential actions.
- Emergency controls support:
  - global stop;
  - scoped stop;
  - safe mode;
  - resource admission closure;
  - audit-write blocking;
  - recovery promotion;
  - controlled restart.
- All control actions are audited with actor, reason, scope, and evidence.

### Implementation areas

- `src/msb_v3/api/auth.py`
- `src/msb_v3/governance/`
- `src/msb_v3/api/governance.py`
- `src/msb_v3/core/container.py`
- new Cockpit control service if the existing composition root is insufficient.

### Verification

- Unauthorized localhost request test.
- Forged actor test.
- Producer/authority separation test.
- Executor/verifier separation test.
- Audit-failure fail-closed test.
- Emergency-control test.
- Recovery-promotion test.

**Reversibility:** Control changes are reversible but consequential; destructive or deployment actions require explicit operator approval.

---

## Phase 7 — Cockpit API and Dashboard

**Intent:** Build a useful operator surface over verified state, without making the UI the authority.

### Cockpit panels

1. **Build and runtime** — version, boot, runtime identity, model identity, native/Rosetta state.
2. **Audit integrity** — chain head, sequence, last checkpoint, external anchor status, verifier verdict.
3. **Storage** — free space, state machine, write queue, WAL status, SSD health signals, archive pressure.
4. **Memory** — physical memory, pressure, swap, process budget, admission state.
5. **Executor** — active workers, queue depth, per-panel budgets, rejection counts, degraded reasons.
6. **Qdrant** — reachability, collection health, restart marker, independent integrity verdict.
7. **Supervisor** — launchd job state, crash-loop count, backoff, safe-mode state.
8. **Authority** — pending approvals, killswitch state, recent refusals, operator actions.
9. **Recovery** — restore points, restore rehearsal status, migration state, open incidents.

### API principles

- Read-only status endpoints may be public only within an explicitly bounded trust surface.
- Consequential actions require operator authorization.
- Every status value has a source of truth and freshness timestamp.
- Cached truth expires when underlying state changes.
- A stale or unavailable dependency is shown as unknown/degraded, never green.
- The UI never calls itself “verified” because internal state looks consistent.

### Implementation areas

- `src/msb_v3/api/cockpit.py` or the existing dashboard surface.
- `src/msb_v3/api/dashboard.py`
- desktop UI files under `desktop/`.
- metrics and observability modules.

### Verification

- API contract tests.
- UI state tests.
- Stale-data and unavailable-dependency tests.
- Accessibility and keyboard checks.
- Browser smoke tests.
- Operator action and authorization tests.

**Reversibility:** UI changes are reversible; any control exposed by the UI remains governed by the control plane.

---

## Phase 8 — Qdrant, Apple Silicon, Docker, and Vector Integrity

**Intent:** Prevent the Cockpit from treating a reachable Qdrant container as a healthy vector store.

### Requirements

- Account for 16 kB pages on Apple Silicon.
- Prefer native arm64 images or explicitly verified images.
- Avoid jemalloc aborts caused by unsupported system page sizes.
- Use named Docker volumes rather than bind mounts where the documented macOS failure mode applies.
- After every Qdrant restart:
  - check daemon/container health;
  - check collection existence;
  - check point counts;
  - check non-zero vectors;
  - compare expected tenant collection inventory;
  - run a sampled deterministic query;
  - record an independent integrity verdict.
- A failed vector check forces degraded mode or service quarantine.
- Vector similarity remains retrieval evidence, never truth authority.

### Artifacts

- Qdrant restart integrity checker.
- Apple Silicon/container compatibility manifest.
- Docker volume and image policy.
- Qdrant incident and recovery runbook.
- Test fixtures with zero-vector and missing-collection failures.

**Reversibility:** Read-only integrity checks are reversible; changing Docker storage layout requires backup and migration approval.

---

## Phase 9 — launchd Supervision and Safe Recovery

**Intent:** Make crash loops, unhealthy restarts, and resource exhaustion visible and bounded.

### Requirements

- Native macOS `launchd` supervision.
- Health-gated restart.
- Exponential backoff and crash-loop detection.
- Safe-mode entry after repeated failures.
- Independent supervisor process or launchd job for audit verification.
- External watchdog capability for Docker/Qdrant daemon recovery.
- Process-level isolation where feasible.
- No supervisor action that silently bypasses audit verification.

### Artifacts

- LaunchAgent plist templates.
- Thin supervisor wrapper.
- Health contract.
- Crash-loop state store.
- Safe-mode entry and exit runbook.
- Restart and recovery evidence logs.

### Verification

- Forced process crash.
- Forced unhealthy response.
- Crash-loop threshold.
- Backoff behavior.
- Safe-mode entry.
- Supervisor restart after recovery.
- Docker daemon failure simulation.

**Reversibility:** Local LaunchAgent changes are reversible; loading or removing production jobs requires operator approval.

---

## Phase 10 — Environment, Migration, and Archival

**Intent:** Preserve the exact runtime context required to interpret historical evidence.

### Environment manifest fields

- macOS version and build.
- Hardware model and architecture.
- Xcode/Command Line Tools version.
- Python version.
- SQLite version and journal settings.
- Qdrant version and image digest.
- Model identifiers and hashes.
- Application version and Git commit.
- Schema versions.
- Relevant configuration fingerprint with secrets removed.

### Migration requirements

- Versioned, forward-only migrations by default.
- Preserve pre-migration chain head.
- Write and verify a post-migration head.
- Fail closed on interrupted migration.
- Record migration events.
- Test historical verification before and after the new runtime is declared healthy.

### Archival requirements

- Rotation only after completed segments are durably checkpointed.
- Archive manifest contains sequence range, head hashes, schema version, and environment manifest.
- Deletion from live storage is permitted only after external verification confirms the archive.
- Archive medium rotation is scheduled and tested.

**Reversibility:** Migrations require snapshot and restore rehearsal; archival is append-first and deletion is approval-gated.

---

## Phase 11 — Full Adversarial and Physical Test Matrix

**Intent:** Execute the interrogation set as an evidence-producing system.

### Test classes

1. Audit deletion, truncation, update, replacement, rollback, and permission tampering.
2. Power loss during append, checkpoint, rotation, migration, and remote push.
3. Disk-full transitions and audit-write refusal.
4. Concurrent writers and copy operations.
5. SQLite WAL recovery and page corruption.
6. External anchor loss, rollback, signature substitution, and stale state.
7. Qdrant restart, zero-vector detection, missing collection, and Apple Silicon page-size behavior.
8. macOS major-upgrade migration.
9. Replacement-host restore.
10. 30+ minute 4P+4E thermal/load run.
11. 8 GB memory-pressure and swap-thrashing run.
12. Operator authority and emergency-control attacks.
13. Test-process access to production paths.
14. Independent verifier run without producer source access.
15. 2036 evidence-package reconstruction rehearsal.

### Evidence result

Each test produces:

```json
{
  "test_id": "...",
  "gate_ids": ["..."],
  "environment": {},
  "result": "PASS | FAIL | BLOCKED | UNKNOWN",
  "artifacts": [],
  "observed_failures": [],
  "verified_by": "",
  "verified_at": "",
  "limitations": []
}
```

`BLOCKED` and `UNKNOWN` are valid, useful outcomes. They must not be converted to `PASS` for appearance.

**Reversibility:** Failure-injection tests require isolated volumes or hosts. They must never target production by default.

---

## Phase 12 — Final 2036 Assurance Package

**Intent:** Package the evidence needed to verify historical guarantees after the original Mac mini is gone.

### Final package contents

- Versioned canonical schema and verifier.
- Signed chain checkpoints.
- Immutable archive segment catalog.
- Environment manifests.
- Migration history.
- Restore procedure and rehearsal evidence.
- Test matrix results.
- Incident and repair history.
- Key rotation/recovery records without secrets.
- Public verification instructions.
- Statement of exactly which claims are and are not proven.

### Final acceptance

The project may claim the strongest practical 2036 property only when:

- a replacement Apple Silicon host can verify the chain without trusting the original host;
- the external anchor remains independently controlled;
- the chain head and sequence are reproducible;
- archive gaps and replacements are detected;
- historical environment context is reconstructable;
- restore has been exercised;
- the independent verifier produces the same result as the production verifier;
- all residual limitations are explicit.

## 6. Execution Cadence

Each phase follows this loop:

```text
SPEC
  → RED TEST
  → IMPLEMENT
  → UNIT TEST
  → INTEGRATION TEST
  → ADVERSARIAL TEST
  → RESOURCE TEST
  → AUDIT TEST
  → INDEPENDENT REVIEW
  → EVIDENCE REVIEW
  → GREEN / REVISE / BLOCKED
```

No phase advances because the code looks complete. It advances only when its exit evidence exists.

## 7. Approval and Authority Gates

The following require explicit operator approval before execution:

- installing or changing launchd jobs;
- enabling Docker or Qdrant in production mode;
- creating or rotating external trust-anchor credentials;
- deleting, truncating, rotating, or migrating production audit data;
- restoring from a backup;
- changing production schema;
- deploying or restarting production services;
- changing emergency-control semantics;
- deleting archived evidence;
- provisioning external infrastructure or storage.

Local tests, isolated fixtures, documentation, and reversible code changes proceed without production approval.

## 8. Rollback Strategy

- Code changes: revert by commit or patch; never mix rollback with evidence deletion.
- Schema changes: retain pre-migration database and chain head until post-migration verification passes.
- Checkpoint changes: additive publication; do not remove prior valid checkpoints.
- Restore: restore into a quarantine path and promote only after verification.
- UI changes: disable the route or revert the UI without disabling the underlying audit writer.
- Supervisor changes: retain the previous LaunchAgent configuration and recovery command.
- Qdrant changes: preserve the named volume and never run destructive cleanup as part of restart verification.
- External media failure: mark the external trust anchor unavailable; do not downgrade to local-only while reporting healthy.

## 9. Roles Required

| Role | Responsibility |
|---|---|
| Principal architect | Authority boundaries, state ownership, migration strategy |
| Security engineer | Cryptography, storage threats, authorization, key rotation |
| SRE | launchd, health, restart, safe mode, incident recovery |
| Data engineer | SQLite/WAL, schema migrations, archival, restore |
| ML/platform engineer | Ollama, Qdrant, Apple Silicon, Docker, memory pressure |
| Backend engineer | API, executor, admission control, event flow |
| Frontend engineer | Cockpit UI, operator workflows, accessibility |
| QA/adversarial engineer | Failure injection, power-loss simulation, physical tests |
| Independent verifier | Runs from a second user/process or second host |
| Operator/owner | Approves consequential actions and signs acceptance evidence |

## 10. Definition of Done

The build is complete only when:

- all 104 gates are represented in the verification manifest;
- every critical guarantee has a mechanical enforcement or an explicit blocked status;
- all automated tests pass;
- all physical tests have been run or are explicitly marked blocked;
- a replacement-host restore has been exercised;
- an independent verifier agrees with the production verifier;
- the Cockpit displays unknown/degraded truthfully;
- no stale cache is presented as current truth;
- no production test process can write to production evidence;
- the final assurance package can be verified years later;
- the team is willing to say “not proven” wherever the evidence is incomplete.

## 11. First Execution Slice

The first implementation slice is intentionally small and boring:

1. Build the verification manifest and traceability report.
2. Specify audit schema v2 and canonicalization vectors.
3. Add the standalone verifier and golden tamper tests.
4. Normalize SQLite durability settings and expose them in health output.
5. Add the storage state machine with injectable free-space measurement.
6. Add external checkpoint acceptance to restore verification.
7. Add focused tests before changing UI or production deployment.

This slice creates the trustworthy foundation on which the larger Cockpit can safely be built.
