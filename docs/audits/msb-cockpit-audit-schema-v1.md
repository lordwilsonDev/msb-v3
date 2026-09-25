# MSB Cockpit Audit Schema Specification

**Schema family:** `msb.audit`  
**Specification version:** 1.0  
**Implementation compatibility:** preserves existing `AuditChain` records; defines the new v2 record contract for migration  
**Date:** 2026-09-24  
**Status:** Implemented as a dependency-free canonicalizer and standalone JSONL verifier; SQLite producer integration and migration remain open

## 1. Purpose

The audit record schema defines what one committed record means, how its bytes are canonicalized, how it is linked to its predecessor, how it is verified independently, and how it is checkpointed and restored.

The schema separates three different things:

1. **History representation** — a local SQLite database or archived segment.
2. **Cryptographic continuity** — hashes linking records and checkpoints.
3. **External authenticity** — a signed checkpoint held outside the local failure domain.

A valid local chain is not automatically an externally authentic chain.

## 2. Version identifiers

| Identifier | Meaning |
|---|---|
| `msb.audit.v1` | Existing compatibility record shape. Hashes remain unchanged. |
| `msb.audit.v2` | New canonical record shape introduced by explicit migration. |
| `msb.checkpoint.v1` | Signed external checkpoint envelope. |
| `msb.environment.v1` | Runtime and dependency manifest attached to checkpoints. |

`schema_version` is mandatory in every v2 record and checkpoint. It is part of the hashed content. A verifier must reject a missing or unsupported version rather than guessing.

## 3. Record envelope

A v2 audit record has this logical shape:

```json
{
  "schema_version": "msb.audit.v2",
  "record_type": "action.audit",
  "seq": 42,
  "recorded_at": "2026-09-24T12:00:00.000000+00:00",
  "component": "vesta",
  "actor": "operator:local",
  "action": "shell.execute",
  "authority": {
    "decision_id": "approval-123",
    "policy_version": "vesta-policy-1",
    "scope": ["sandbox:/runtime/vesta"]
  },
  "execution": {
    "executor_id": "executor-main",
    "result": "success"
  },
  "verification": {
    "verifier_id": "independent-verifier",
    "result": "verified",
    "method": "external-chain-check"
  },
  "payload": {},
  "environment_ref": "env-2026-09-24T12:00:00Z",
  "prev_hash": "0000000000000000000000000000000000000000000000000000000000000000",
  "record_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
}
```

The `record_hash` field is excluded from the preimage. The verifier recomputes it and stores/compares it separately.

## 4. Required fields

| Field | Type | Required | Rules |
|---|---|---:|---|
| `schema_version` | string | yes | Exactly `msb.audit.v2` for new records. |
| `record_type` | string | yes | Non-empty, namespaced, stable across releases. |
| `seq` | integer | yes | Strictly positive and contiguous within a segment. |
| `recorded_at` | RFC 3339 string | yes | UTC, explicit offset, finite and parseable. |
| `component` | string | yes | Producing subsystem; not an authority claim. |
| `actor` | string | yes | Authenticated or explicitly marked `system`; never silently caller-supplied. |
| `action` | string | yes | Stable action identifier. |
| `authority` | object | yes | Authority decision and scope; `system` actions may use an explicit system authority. |
| `execution` | object | yes | Executor identity and bounded result classification. |
| `verification` | object | yes | Verifier identity and verdict. |
| `payload` | object | yes | Action-specific data; bounded and schema-validated. |
| `environment_ref` | string | yes | Immutable environment manifest reference or digest. |
| `prev_hash` | 64 lowercase hex characters | yes | Immediate predecessor hash; genesis is 64 zeroes. |
| `record_hash` | 64 lowercase hex characters | yes | SHA-256 digest of the canonical preimage. |

Unknown top-level fields MUST be rejected by a strict verifier unless the schema version explicitly permits extensions. This prevents a producer from hiding semantically important state in an unverified field.

## 5. Canonical serialization

### 5.1 Input restrictions

Before serialization, the producer MUST:

- convert objects to JSON-compatible values;
- reject `NaN`, `Infinity`, and `-Infinity`;
- reject non-string object keys;
- reject unsupported binary values;
- reject duplicate keys after parsing;
- normalize timestamps to UTC RFC 3339 with an explicit offset;
- preserve integers without lossy floating-point conversion;
- reject payload values above configured size limits.

### 5.2 Canonical byte algorithm for v2

1. Construct the hashed object containing all required fields except `record_hash`.
2. Serialize with UTF-8.
3. Sort object keys lexicographically by Unicode code point.
4. Use no insignificant whitespace: `,` between members and `:` between key and value.
5. Use JSON string escaping compatible with RFC 8785 for the supported value set.
6. Encode the resulting characters as UTF-8 without a BOM.
7. Hash the bytes with SHA-256.
8. Store the lowercase hexadecimal digest as `record_hash`.

Conceptually:

```text
canonical_record = JCS(record_without_record_hash)
record_hash = SHA256(UTF8(canonical_record))
```

The v2 implementation MUST use a library or a reviewed pure implementation that has golden vectors for:

- nested objects;
- Unicode keys and values;
- escaped strings;
- integers;
- booleans and null;
- empty objects and arrays;
- key ordering;
- non-ASCII UTF-8;
- malformed and duplicate input.

### 5.3 Existing v1 compatibility

Existing `AuditChain` records use a historical Python `json.dumps(..., sort_keys=True, ensure_ascii=False)` representation. A v2 migration MUST NOT reinterpret or rewrite existing hashes as v2 records.

The migration MUST:

1. identify v1 rows using the recorded schema metadata or migration ledger;
2. preserve the original payload and timestamp bytes or values;
3. recompute the old hash using the v1 algorithm;
4. write new v2 records as new linked records or a separately versioned segment;
5. preserve the v1 head as an explicit anchor point;
6. record the migration event in both old and new evidence bundles.

## 6. Hash chaining

For sequence `n`:

```text
prev_hash(n) = record_hash(n - 1)
prev_hash(1) = 0x00 * 32
```

The chain verifier MUST:

- read records in sequence order;
- reject duplicate sequence numbers;
- reject gaps unless the gap is explicitly represented by a segment manifest and checkpoint;
- reject negative or zero sequence values except an explicitly defined genesis record;
- reject non-hex or wrong-length hashes;
- recompute each record hash from canonical content;
- compare each predecessor hash with the preceding record;
- report the first failing sequence and reason;
- return `UNKNOWN` or `BLOCKED` rather than healthy when evidence is incomplete.

A segment boundary is valid only when the segment manifest records:

- first and last sequence;
- predecessor hash at the boundary;
- head hash;
- checkpoint signature;
- schema version;
- environment manifest reference.

## 7. Checkpoint envelope

A checkpoint contains:

```json
{
  "schema_version": "msb.checkpoint.v1",
  "checkpoint_seq": 17,
  "chain_head_seq": 4096,
  "chain_head_hash": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "segment": "audit-000004.jsonl",
  "segment_first_seq": 4001,
  "segment_last_seq": 4096,
  "environment": {},
  "notarized_at": "2026-09-24T12:00:00Z",
  "signature": {
    "algorithm": "ECDSA-P256-SHA256",
    "public_key_id": "anchor-key-2026-01",
    "value": "base64..."
  }
}
```

A checkpoint is invalid if its head sequence/hash does not correspond to the verified chain, its signature is invalid, its environment manifest is incomplete, or its segment range is not justified by the archive catalog.

## 8. Storage invariants

The SQLite database is a local representation, not the external authenticity authority. Its contract is:

- audit records are append-only at the storage layer;
- update and delete are refused by normal application paths;
- the sequence and hash preimage are written in one transaction;
- `fsync`/synchronous behavior is configured and verified;
- WAL recovery either yields a valid chain or an explicit failure;
- a write failure is not reported as a successful append;
- storage pressure transitions to `AUDIT_WRITE_BLOCKED` before silent loss;
- safe mode cannot be bypassed by a stale UI or cache;
- migration preserves the pre-migration head and writes a post-migration head.

SQLite triggers are a guardrail, not a claim of physical immutability. A user with filesystem control can replace the database. That is why checkpoints and independent verification are mandatory.

## 9. Verification levels

| Level | Can establish | Cannot establish |
|---|---|---|
| Local parse | JSON validity | authenticity or completeness |
| Local chain | internal hash continuity | external replacement detection |
| Signed checkpoint | producer key signed a head | that the producer was not compromised |
| Off-box checkpoint | external copy of signed head | physical future retention of media |
| Second-host verification | independent reproducibility on replacement host | future security of every future tool |
| 2036 assurance package | recoverable, bounded, independently verifiable evidence under stated assumptions | guarantees beyond those assumptions |

## 10. Acceptance tests

The schema is ready for implementation only when these tests exist and pass:

### Canonicalization

- AC-CAN-001: same semantic object with different key order produces identical bytes.
- AC-CAN-002: Unicode is encoded as UTF-8 and hashed consistently.
- AC-CAN-003: whitespace differences do not change the digest.
- AC-CAN-004: `NaN`, `Infinity`, duplicate keys, and unsupported values are rejected.
- AC-CAN-005: v1 compatibility vectors retain their historical digests.

### Chain

- AC-CHAIN-001: genesis predecessor is 64 zeroes.
- AC-CHAIN-002: sequence increments by one inside a segment.
- AC-CHAIN-003: a content substitution fails at the changed record.
- AC-CHAIN-004: a predecessor substitution fails at the link.
- AC-CHAIN-005: deletion produces a gap or a continuity failure.
- AC-CHAIN-006: reordering fails verification.
- AC-CHAIN-007: concurrent appends cannot fork the chain.
- AC-CHAIN-008: a segment manifest validates a legal boundary.

### Checkpoint and restore

- AC-CHECK-001: checkpoint carries sequence and head hash.
- AC-CHECK-002: signature verification rejects altered checkpoint fields.
- AC-CHECK-003: remote-head verification detects local rollback.
- AC-CHECK-004: restore remains quarantined until external verification passes.
- AC-CHECK-005: a replacement-host verifier reaches the same accepted head.

### Migration

- AC-MIG-001: pre-migration head is retained and signed.
- AC-MIG-002: post-migration head is written and signed.
- AC-MIG-003: interrupted migration is explicit and recoverable.
- AC-MIG-004: historical v1 records remain independently verifiable.

## 11. Golden vectors

Golden vectors live in:

```text
tests/fixtures/audit_golden_vectors.json
```

Each vector contains:

- vector ID and description;
- schema version;
- record input excluding `record_hash`;
- expected canonical bytes or a canonical-byte digest;
- expected record hash;
- mutation cases that must fail.

The fixture is a test contract for the standalone v2 verifier. It is not evidence that the current SQLite `AuditChain` producer writes v2 records; producer integration and migration remain separate gates.

## 12. Versioning and migration policy

- Schema versions are immutable after release.
- A new version requires a migration spec and a new test vector family.
- Existing records are never silently rehashed.
- Migration is append-only whenever possible.
- A failed migration must not change the authoritative head.
- The release claim layer must identify which schema version it supports.
- Historical verification must remain possible with the original verifier and documented algorithm.

## 13. Open decisions requiring implementation evidence

- Choose the exact RFC 8785-compatible JSON implementation already present in the project, or document a reviewed fallback.
- Define the maximum record and payload size by measured SSD, latency, and memory constraints.
- Choose checkpoint cadence based on recovery point objective and write endurance.
- Define the external sink's WORM/object-lock contract and media rotation interval.
- Define the second-host minimum OS/runtime compatibility policy.
- Define whether a v2 record is appended after every v1 record during migration or only at controlled migration boundaries.
