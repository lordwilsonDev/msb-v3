# MSB Cockpit Execution Slice 2 Plan v1.0

**Status:** Draft for execution  
**Date:** 2026-09-24  
**Parent plan:** `docs/audits/msb-cockpit-build-plan-v1-2026-09-24.md`  
**Scope:** All three requested next slices

1. Read-only v1 audit-chain exporter
2. Controlled v1-to-v2 migration barrier
3. SQLite durability and storage-state contract

## 1. Outcome

Produce a reversible bridge from the existing historical audit chain to the new independently verifiable v2 evidence path, without rewriting or silently changing historical records.

The slice must leave the system in one of these explicit states:

```text
LEGACY_V1_ACTIVE
  → V1_EXPORTED
  → V1_HEAD_SIGNED
  → MIGRATION_BARRIER_ACTIVE
  → V2_SEGMENT_ACTIVE
  → V2_EXPORT_VERIFIED
```

A failed or interrupted migration must return to a quarantined/blocked state. It must never silently fall back to “healthy v1” after v2 publication has begun.

## 2. Confirmed starting point

The repository already has:

- `src/msb_ledger/audit_chain.py` with the historical v1 SQLite chain.
- `src/msb_ledger/audit_v2.py` with the dependency-free v2 canonicalizer and standalone JSONL verifier.
- `tests/ledger/test_audit_v2.py` and `tests/fixtures/audit_golden_vectors.json`.
- `src/msb_ledger/chain_anchor.py` for signed head anchors.
- `src/msb_ledger/notary.py` for local/remote checkpoint notarization.
- `src/msb_ledger/merkle.py` for inclusion proofs.

The existing SQLite producer still writes historical v1-shaped records. No production write path is changed by this plan until the migration barrier is proven.

## 3. Non-negotiable safety rules

1. The v1 exporter is read-only and must not open the production database for writes.
2. The exporter must not call `repair()`, `anchor()`, or any mutating helper.
3. Historical v1 hashes must remain unchanged.
4. The migration barrier must preserve the v1 head hash and sequence in signed evidence.
5. A v2 segment must not begin until the v1 export and signed v1 head pass verification.
6. A failed export, missing sequence, malformed payload, or unverifiable head blocks migration.
7. SQLite storage pressure must fail closed before an audit write is reported as successful.
8. Tests use isolated databases, volumes, or processes. They do not target the live production store.
9. No physical claim is made from software simulation.
10. The exporter, migration, and durability changes are independently reviewable and reversible.

## 4. Dependency graph

```text
A. v1 exporter
   ├── requires read-only SQLite connection
   ├── emits v1 JSONL export
   └── produces v1 export manifest + digest

B. migration barrier
   ├── requires A export
   ├── requires v1 chain verification
   ├── requires signed v1 head
   ├── requires clean v2 writer contract
   └── produces v2 segment only after barrier

C. SQLite durability/storage state
   ├── must exist before A opens production store
   ├── must exist before B changes schema/write mode
   ├── must fail closed under disk pressure
   └── supplies health/status to Cockpit later
```

C is implemented as a shared foundation before B is allowed to write v2 records. A can be developed in parallel with C, but its production use is gated on C passing.

## 5. Slice A — Read-only v1 audit-chain exporter

### 5.1 Intent

Export historical v1 records as immutable, verifiable JSONL without changing the source database or its hashes.

### 5.2 Proposed implementation

Add a narrow module:

```text
src/msb_ledger/audit_v1_export.py
```

Add a CLI:

```text
python -m msb_ledger.audit_v1_export \
  --database PATH \
  --output PATH.jsonl \
  --manifest PATH.json
```

The CLI must require an explicit database path and output path. It must refuse to write inside the source database directory unless a separate export directory is explicitly supplied.

### 5.3 Export contract

Each JSONL line contains:

```json
{
  "schema_version": "msb.audit.v1-export",
  "seq": 1,
  "component": "...",
  "event_type": "...",
  "payload": {},
  "timestamp": "...",
  "prev_hash": "...",
  "record_hash": "..."
}
```

The exporter must:

- open SQLite read-only using `mode=ro` or an equivalent immutable/read-only URI;
- set a busy timeout;
- read rows in ascending sequence order;
- reject duplicate or non-contiguous sequences;
- reject malformed JSON payloads;
- recompute the historical v1 hash for every row;
- compare recomputed and stored hashes;
- compare predecessor links;
- write each line with a final newline;
- flush and `fsync` the output before writing the manifest;
- write the manifest only after the JSONL is complete and durable;
- leave the source database unchanged.

### 5.4 Export manifest

The manifest must contain:

```json
{
  "schema_version": "msb.audit.v1-export-manifest",
  "source_db": "redacted-or-approved-path",
  "source_db_sha256": "optional-file-digest-policy",
  "first_seq": 1,
  "last_seq": 4096,
  "record_count": 4096,
  "first_prev_hash": "...",
  "head_hash": "...",
  "output_sha256": "...",
  "exported_at": "...",
  "source_mutation_during_export": false
}
```

If the source database changes during export, the manifest must be marked invalid and the output must not be promoted.

### 5.5 Acceptance criteria

- AC-A-001: read-only export produces the same sequence and hashes as the source.
- AC-A-002: direct UPDATE/DELETE against the source is never attempted by exporter code.
- AC-A-003: a changed source row causes export failure.
- AC-A-004: a missing sequence causes export failure.
- AC-A-005: a malformed payload causes export failure.
- AC-A-006: a partial output is never accompanied by a valid manifest.
- AC-A-007: repeated export produces identical JSONL bytes and manifest fields except export timestamp.
- AC-A-008: the source DB mtime/size/digest policy is recorded and enforced.
- AC-A-009: a clean subprocess can verify the exported v1 data using the documented v1 algorithm.

### 5.6 Tests

- Unit tests for JSON serialization and manifest construction.
- Read-only connection test using file permissions or SQLite URI.
- Concurrent source mutation test.
- Sequence gap test.
- Payload corruption test.
- Truncated output test.
- Repeated-export determinism test.
- Clean-process verification test.

### 5.7 Exit gate

The exporter is promotable only when a test chain can be exported, the source hash remains unchanged, a second process verifies the export, and a deliberate output truncation is rejected.

## 6. Slice B — Controlled v1-to-v2 migration barrier

### 6.1 Intent

Move new audit writes to `msb.audit.v2` without rewriting or reinterpreting historical v1 records.

### 6.2 Decision: controlled barrier, not in-place rewrite

The approved migration shape is:

1. Stop new v1 writes at a controlled barrier.
2. Acquire the SQLite writer lock.
3. Verify the complete v1 chain.
4. Export the complete v1 chain.
5. Verify the export independently.
6. Sign and notarize the v1 head, including sequence and hash.
7. Create a migration manifest containing both v1 and v2 heads.
8. Start the first v2 record with `prev_hash` equal to the signed v1 head.
9. Mark the chain state `MIGRATING` until the first v2 record is externally verifiable.
10. Mark the chain state `V2_ACTIVE` only after v2 verification and checkpoint success.

If any step fails, the chain state becomes `MIGRATION_BLOCKED`.

### 6.3 Migration state machine

```text
V1_ACTIVE
  → V1_BARRIER
  → V1_EXPORTED
  → V1_HEAD_SIGNED
  → V2_INITIALIZING
  → V2_ACTIVE
```

Failure transitions:

```text
ANY_STATE
  → MIGRATION_BLOCKED
```

`MIGRATION_BLOCKED` permits verification and recovery operations only. It does not permit ordinary appends.

### 6.4 Migration manifest

The manifest must contain:

- migration ID;
- v1 first/last sequence;
- v1 head hash;
- v1 export digest;
- v1 checkpoint signature reference;
- v2 first sequence;
- v2 genesis predecessor hash;
- v2 schema version;
- application version;
- operator identity;
- started/completed timestamps;
- environment manifest reference;
- result status.

### 6.5 Schema behavior

Do not add v2 columns to the existing v1 table in place. Use either:

- a new `audit_records_v2` table in a migrated database, or
- a separate v2 segment store with an explicit segment manifest.

The chosen design must preserve:

- v1 table readability;
- v1 hash verification;
- v2 sequence allocation;
- predecessor link from v1 head to v2 first record;
- independent export of both versions;
- no cross-version row mutation.

### 6.6 Acceptance criteria

- AC-B-001: pre-migration v1 head is signed before any v2 record.
- AC-B-002: first v2 `prev_hash` equals the signed v1 head.
- AC-B-003: v1 historical hashes are unchanged after migration.
- AC-B-004: an interrupted barrier never marks v2 active.
- AC-B-005: an unverified v1 export blocks migration.
- AC-B-006: a missing v1 checkpoint blocks migration.
- AC-B-007: a second process verifies the v2 segment independently.
- AC-B-008: replay can read both versions and report one continuous causal history.
- AC-B-009: migration restart is idempotent and cannot create a second first-v2 record.
- AC-B-010: the operator can abort before v2 activation without deleting v1 history.

### 6.7 Tests

- Happy-path barrier with a temporary chain.
- Crash after v1 export.
- Crash after v1 head signing.
- Crash after v2 first insert.
- Concurrent writer attempt during barrier.
- Missing external checkpoint.
- Older v1 backup restoration.
- Duplicate migration invocation.
- v1 historical hash regression.
- v2 independent verification in a clean subprocess.

### 6.8 Exit gate

Migration is not promotable until a crash-injection suite proves that every interruption is either safely resumable or visibly blocked, with no silent v2 activation.

## 7. Slice C — SQLite durability and storage-state contract

### 7.1 Intent

Make SQLite behavior explicit, bounded, observable, and fail-closed under power loss, disk pressure, corruption, and concurrency.

### 7.2 Shared connection contract

Introduce or extend one connection factory for audit storage with documented settings:

```text
journal_mode = WAL
synchronous = FULL or explicitly justified alternative
busy_timeout = bounded value
foreign_keys = ON
wal_autocheckpoint = bounded value
cache_size = bounded value
```

The exact values must be measured against latency and write-endurance tests. The contract must expose the effective values through a read-only health function.

### 7.3 Storage state machine

Implement the exact state machine from the interrogation set:

```text
NORMAL
  → LOW_SPACE
  → CRITICAL_SPACE
  → AUDIT_WRITE_BLOCKED
  → SAFE_MODE
```

Recommended initial thresholds, to be confirmed by measurement:

- `LOW_SPACE`: 15% free or configured warning threshold.
- `CRITICAL_SPACE`: 10% free or configured archival threshold.
- `AUDIT_WRITE_BLOCKED`: 5% free, WAL growth risk, or injected storage failure.
- `SAFE_MODE`: operator-selected recovery mode or repeated write failure.

The thresholds are configuration only after the state machine and fail-closed behavior are tested. Configuration must never bypass a mechanical refusal.

### 7.4 Write admission

Every audit append must pass:

1. chain integrity preflight;
2. storage state check;
3. bounded queue admission;
4. SQLite transaction;
5. durable commit;
6. post-write sequence/hash readback;
7. checkpoint freshness check where required.

A failure at any step must return a structured refusal. The caller must not receive an `AuditRecord` for an uncommitted write.

### 7.5 Disk-full and corruption behavior

- `ENOSPC` transitions to `AUDIT_WRITE_BLOCKED`.
- An unwriteable state transition is surfaced through a separate health channel.
- SQLite `database disk image is malformed` is never converted to healthy.
- WAL corruption is reported separately from audit-chain corruption.
- Recovery operations open the store in quarantine mode.
- No automatic repair runs during normal startup.

### 7.6 Acceptance criteria

- AC-C-001: effective SQLite durability settings are queryable.
- AC-C-002: two concurrent writers do not fork the chain.
- AC-C-003: an injected full-disk condition blocks authoritative appends.
- AC-C-004: a database corruption condition produces degraded/blocked health.
- AC-C-005: WAL recovery either yields a valid chain or explicit failure.
- AC-C-006: a process crash does not report a successful append before commit.
- AC-C-007: free-space transitions follow the exact state order.
- AC-C-008: safe mode refuses ordinary writes.
- AC-C-009: checkpoint metadata records the effective storage contract.
- AC-C-010: a test process cannot write to the production database.

### 7.7 Tests

- PRAGMA contract test.
- Concurrent writer test.
- Read concurrency test.
- WAL checkpoint/restart test.
- Subprocess termination test.
- Injected disk-full test.
- Malformed database test.
- Corrupt WAL test.
- State transition table test.
- Safe-mode write refusal test.
- Bounded queue and rate-limit test.
- Physical 16 GB resource test, when hardware is available.

### 7.8 Exit gate

Storage is promotable only when no failure path can return a successful append result, safe mode is mechanical rather than UI-only, and health output distinguishes nominal, degraded, blocked, and unknown states.

## 8. Implementation order

### Stage 0 — Red tests and interfaces

- Add test fixtures for isolated v1 chains.
- Add read-only export contract tests.
- Add migration barrier state tests.
- Add SQLite PRAGMA and failure-injection tests.
- Document API and state-machine interfaces before implementation.

### Stage 1 — Exporter

- Implement read-only export.
- Verify deterministic output.
- Verify source immutability.
- Export a real temporary chain.
- Run a clean subprocess verification.

### Stage 2 — SQLite contract

- Implement connection settings.
- Implement health reporting.
- Implement storage state transitions.
- Implement write admission and structured refusals.
- Run all hermetic failure tests.

### Stage 3 — Migration barrier

- Implement migration manifest.
- Implement barrier lock.
- Implement v1 export/signature gate.
- Implement v2 segment initialization.
- Implement resumable and blocked states.
- Run crash-injection tests.

### Stage 4 — Integration

- Export v1 records from an isolated chain.
- Sign the v1 head.
- Create a v2 segment.
- Verify both formats independently.
- Replay the combined history.
- Update the 104-gate manifest with evidence.

### Stage 5 — Physical validation

- Run on the target Mac mini.
- Test 8 GB and 16 GB memory pressure.
- Test abrupt wall-power removal.
- Test external trust-anchor media.
- Test a second Apple Silicon host.
- Test macOS major-version migration.

## 9. Approval gates

Approval is required before:

- changing the production database schema;
- stopping or restarting production writers;
- signing a production v1 head;
- creating a production v2 segment;
- enabling a production storage state threshold;
- restoring or replacing production data;
- rotating the external trust anchor;
- running physical power-loss or SSD-failure tests;
- deploying a new launchd job.

Local tests, temporary databases, and reversible documentation/code changes do not require production approval.

## 10. Rollback

### Exporter

- Delete or quarantine only the temporary export output.
- Never modify the source database.
- A failed export is evidence, not a reason to overwrite the source.

### Migration

- Before v2 activation: abort the barrier and remain on verified v1.
- After v2 activation: stop writers, preserve both segments, verify the v2 head, and roll forward only through an explicit operator-controlled migration.
- Never delete v1 records to make a v2 replay appear clean.

### SQLite durability

- Revert code configuration only after confirming no live data migration is in progress.
- Never lower synchronous durability or bypass a blocked state to make an application start.
- Keep a pre-change database snapshot and its verified manifest.

## 11. Definition of done for this slice

The three requested workstreams are complete only when:

- a v1 chain exports deterministically without source mutation;
- a clean independent process verifies the export;
- a controlled migration preserves the v1 head and starts v2 correctly;
- crash injection never silently activates v2;
- SQLite effective settings are visible and tested;
- disk-full and corruption conditions fail closed;
- all tests are green;
- the execution log distinguishes code evidence from physical evidence;
- the 104-gate manifest is updated with honest statuses;
- no production migration or physical test is implied by a hermetic test.

## 12. Immediate next implementation checkpoint

Start with **Stage 0: red tests and interfaces** for all three slices. Do not begin the production migration until those tests and the state-machine contracts are reviewed.
