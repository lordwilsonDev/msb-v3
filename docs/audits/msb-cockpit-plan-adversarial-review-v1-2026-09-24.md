# MSB Cockpit Plan Adversarial Review v1

**Review date:** 2026-09-24  
**Review target:** `docs/audits/msb-cockpit-build-plan-v1-2026-09-24.md`  
**Related artifacts:**

- `config/cockpit_verification_manifest.yaml`
- `docs/audits/msb-cockpit-verification-matrix-v1.md`
- `docs/audits/msb-cockpit-audit-schema-v1.md`
- `docs/audits/msb-cockpit-build-verification-interrogation-2026-09-24.md`

**Verdict:** **REVISE BEFORE IMPLEMENTATION** — the plan is directionally sound and correctly refuses to convert unverified physical claims into green status, but several important controls are still proposals rather than implemented guarantees.

## 1. What was checked

- Gate coverage and traceability from the interrogation set to the manifest.
- Whether the plan separates local code evidence from physical and independent evidence.
- Whether the audit schema has deterministic fields, canonicalization, hash chaining, checkpointing, and migration rules.
- Whether the plan identifies approval boundaries, rollback paths, and unverified dependencies.
- Whether the artifacts themselves contain a hidden completion claim.
- Whether a failing or incomplete gate can be represented honestly.

This review did not run the full test suite, power-loss hardware, Qdrant restart, second-host restore, or macOS upgrade rehearsal. Those remain blocked or unverified.

## 2. Findings

### F-001 — The manifest is not yet a CI enforcement gate — HIGH

**Attack:** A future implementation adds code, changes a dependency, or removes a control while leaving the manifest unchanged. The dashboard still displays the old “planned” or “partially evidenced” state, but no automatic gate fails.

**Evidence status:** Confirmed planning gap; CI enforcement not verified in this run.

**Why it matters:** A manifest is evidence inventory, not enforcement. The plan must not treat a YAML file as a control by itself.

**Mitigation:**

- add CI validation that rejects unclassified gates;
- add traceability checks for requirements, tests, and evidence;
- add a release gate that refuses unsupported integrity claims;
- make every implementation change reference at least one gate ID.

**Required evidence:** CI run showing mutation of a gate or missing trace link fails closed.

---

### F-002 — SQLite append-only triggers do not protect the database from filesystem replacement — HIGH

**Attack:** An actor with filesystem authority drops the triggers, replaces the database, restores an older valid database, or swaps the directory.

**Evidence status:** Confirmed from the threat model; the existing trigger implementation is not sufficient alone.

**Why it matters:** SQLite triggers protect the normal SQL mutation path, not the filesystem authority that owns the database file.

**Mitigation:** Require a signed external checkpoint and a verifier that reads the remote head. The restore path must remain quarantined until the external chain validates. The plan already calls this out; Phase 1–4 must prove it mechanically.

**Required evidence:** Whole-DB replacement, directory swap, trigger-drop, and older-snapshot restore tests.

---

### F-003 — The current implementation anchors may be mistaken for complete P0 controls — HIGH

**Attack:** Existing `audit_chain.py`, `chain_anchor.py`, and `notary.py` are interpreted as proof of the complete 104-gate product because the modules and tests exist.

**Evidence status:** Confirmed risk from repository structure; direct implementation proof remains necessary per control.

**Why it matters:** The manifest contains `confirmed_in_code` and `partially_evidenced` states, and the matrix correctly warns that this does not equal physical proof. A release summary could still collapse those states.

**Mitigation:** Keep the distinction in API, UI, release notes, and automated claim checks. Use `confirmed_in_code` only for the narrow implementation statement it names, not for a full gate guarantee.

**Required evidence:** A release gate that scans claims and rejects “ten-year recoverable,” “append-only,” “independent,” or “physically verified” unless the corresponding manifest gates and evidence are complete.

---

### F-004 — The v2 migration strategy has a dangerous unresolved fork — HIGH

**Attack:** The producer begins writing v2 records while existing v1 records are still being appended, or a migration rewrites historical v1 hashes in place.

**Evidence status:** Confirmed specification ambiguity.

**Why it matters:** In-place reinterpretation can break historical verification. Mixed-version appends can make sequence and schema semantics ambiguous.

**Mitigation:** Choose one of these before implementation:

1. freeze v1 writes, create a signed v1 head, then start a v2 segment;
2. append a migration event in v1 and begin v2 after a controlled barrier;
3. use a dual-write bridge with explicit reconciliation.

The recommended default is a controlled barrier plus append-only v2 segment. The choice must be encoded in the migration ledger.

**Required evidence:** Interrupted migration, concurrent writer, rollback, and historical replay tests.

---

### F-005 — Canonicalization is specified more strongly than it is currently implemented — HIGH

**Attack:** Python serialization, Unicode normalization, number handling, duplicate keys, or escaping differs between the producer and the independent verifier. The chain verifies locally but not elsewhere.

**Evidence status:** Confirmed risk; existing code comments explicitly defer a full canonicalization migration.

**Mitigation:** Implement one reviewed v2 canonicalizer, publish golden vectors, and run the verifier in a clean process with no producer imports. Do not claim RFC 8785 compatibility until the vectors pass.

**Required evidence:** Cross-process and alternate-implementation vectors, including Unicode, escapes, integers, malformed input, and duplicate-key rejection.

---

### F-006 — External notary configuration is not equivalent to WORM — HIGH

**Attack:** The remote is a mutable object store, and a sufficiently privileged actor deletes or overwrites the remote objects and the local anchor together. The system still reports local consistency.

**Evidence status:** Confirmed limitation; the existing notary code describes object-lock/WORM as a configuration target, not a universal property.

**Mitigation:** Record the sink capability explicitly as `same_box`, `remote_mutable`, or `worm_object_lock`. Only the last may support the strongest external-authenticity claim. Add a scheduled media/domain rotation test.

**Required evidence:** Sink capability manifest, remote deletion test, remote overwrite test, and replacement-medium rehearsal.

---

### F-007 — The physical test plan has an equipment dependency — BLOCKED

**Attack:** The team marks power-loss, SSD replacement, macOS upgrade, thermal, or second-host gates green using software simulation and then treats the result as hardware proof.

**Evidence status:** Confirmed blocker.

**Mitigation:** Keep the physical gates in `physical_test_required` or `blocked_external_dependency` until the equipment is available. Software failure injection is useful but must be labelled as such.

**Required evidence:** Target Mac mini, controlled power interruption, external media, and second Apple Silicon host.

---

### F-008 — Qdrant health can be falsely green after restart — HIGH

**Attack:** Docker reports healthy, the collection exists, but points are zeroed, the expected tenant collection is missing, or a sampled query is invalid.

**Evidence status:** Confirmed risk; the existing Qdrant contract is a preflight, not proof of the full post-restart integrity gate.

**Mitigation:** Make independent vector integrity a mandatory health transition. Qdrant reachability alone must never satisfy the service-health contract.

**Required evidence:** Missing collection, zero vector, wrong point count, wrong tenant, and deterministic sample-query fixtures.

---

### F-009 — Cockpit freshness can hide recovery state — MEDIUM

**Attack:** A cached dashboard shows “healthy” after storage enters `AUDIT_WRITE_BLOCKED`, memory pressure enters degraded mode, Qdrant integrity fails, or the external anchor is unreachable.

**Evidence status:** Confirmed design requirement; freshness implementation remains open.

**Mitigation:** Every panel status includes source timestamp, freshness age, and dependency verdict. A stale or unavailable source renders as `UNKNOWN` or `DEGRADED`, never healthy.

**Required evidence:** Source mutation while the UI is open, network loss, stale cache, and restart-state tests.

---

### F-010 — The plan is broad enough to become ceremonial — MEDIUM

**Attack:** The team creates the manifest, runbooks, and dashboards but never closes the highest-risk physical or independent gates.

**Evidence status:** Confirmed project-risk pattern from the repository’s existing audit documents.

**Mitigation:** Every phase has an exit gate and artifact class. The plan explicitly defines a small first slice. Add a weekly blocked-gate report and do not expand features while a P0 gate is red for an unrecorded reason.

**Required evidence:** Traceability report, blocked-gate list, and phase exit results.

## 3. Claims that must remain prohibited

Until the corresponding evidence exists, do not state:

- the audit store is physically immutable;
- the system has ten-year recoverability;
- a local-only checkpoint provides external authenticity;
- a passing SQLite test proves power-loss safety;
- Qdrant is healthy because its process is running;
- a dashboard is current because it rendered successfully;
- a second verifier agrees unless it ran without producer write access;
- a replacement host is supported before a real replacement-host rehearsal.

## 4. Test attacks that should be implemented first

The following should be the first red tests:

1. Remove the external anchor and assert restore cannot become authoritative.
2. Drop SQLite triggers, update a record, and assert independent verification fails.
3. Restore an older valid database and assert remote-head verification detects divergence.
4. Interrupt a migration and assert no silent promotion occurs.
5. Fill the storage boundary and assert `AUDIT_WRITE_BLOCKED`, not successful append.
6. Remove the external anchor during restore and assert the result is `BLOCKED`.
7. Restart Qdrant with a zero-vector fixture and assert health is not green.
8. Make the verifier run without importing producer canonicalization code.
9. Mutate source state while the Cockpit is open and assert freshness changes.
10. Attempt a localhost-only unauthorized control action and assert refusal.

## 5. Review disposition

| Area | Disposition |
|---|---|
| Scope | Accept with phased P0–P3 ordering |
| Safety | Accept; destructive and physical actions remain approval-gated |
| Correctness | Revise until v2 canonicalization and migration strategy are implemented |
| Completeness | Accept as a plan; not complete as a product |
| Security | Revise until external sink and verifier independence are tested |
| Uncertainty | Accept; blockers are explicitly labelled |
| Reversibility | Accept for local changes; restore and production changes remain quarantined/approval-gated |

## 6. Final review verdict

The plan is suitable as a controlled build roadmap, not as a completion claim.

The next safe action is the first execution slice in the plan:

1. implement the verification manifest validation;
2. specify and implement v2 canonicalization;
3. add golden vectors with real digests;
4. add an independent verifier;
5. run focused tests;
6. only then begin storage-state and restore work.

Physical and second-host gates remain open and must not be simulated into success.
