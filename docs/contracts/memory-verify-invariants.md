# MEMORY_VERIFY SECURITY INVARIANTS

**Component:** `msb_v3.memory_fabric.verify_memory` + `mcp_bridge._mf_verify`  
**Version:** 1.0 (Phase 6 hardening)  
**Status:** FROZEN — implementation must satisfy all invariants

---

## INV-01 — Authentication Required

Every externally initiated verification transition MUST have an authenticated actor.

```
transition initiated
    ↓
authenticated_actor != null
    ↓
authenticated_actor != ""
    ↓
authenticated_actor != "unknown"
    ↓
authenticated_actor != whitespace-only
```

Failure: reject with 401 (bridge) or ValueError (fabric).

---

## INV-02 — Actor Authority (Caller-Supplied Identity is Not Identity)

Caller-supplied identity MUST NOT determine audit identity.

```
requested_by  ≠  effective audit identity
```

The `by` field in the request payload is **request metadata**, not authentication.

```
request.by  ──→  logged as "requested_by"
context.actor ──→  recorded as "by" in audit trail
```

If `request.by` differs from `context.actor`, both are recorded but only `context.actor` is authoritative.

**Never:** `audit.by = request.by` without validation.

---

## INV-03 — Actor Validity

Reject actors that are:
- `null` / `None`
- empty string
- `"unknown"`
- whitespace-only
- containing control characters

Normalization: strip() → non-empty → not "unknown" → valid.

---

## INV-04 — Contradiction Resolution Gate

```
CONTRADICTED → VERIFIED
```

requires:

```
resolution != null
resolution != empty after strip
resolution passes sanitization (no control chars)
```

The reason field alone is insufficient. A structured resolution evidence string is required.

If resolution is missing or empty: reject with ValueError / 422.

---

## INV-05 — State Legality

Only transitions in the explicit matrix are permitted.

```
                 TO
             U    V    C    D
FROM
U            —    ✓    ✓    ✓
V            —    —    ✓    ✓
C            —    ✓*   —    ✓
D            —    —    —    —
```

```
✓* = requires resolution evidence (INV-04)
```

Any transition not in this matrix is rejected regardless of caller arguments.

---

## INV-06 — Immutable Verification History

Verification history is append-only.

```
for all e in history:
    e.id is unique and never reused
    e.from_state, e.to_state, e.by, e.reason, e.resolution are immutable after insertion
```

No UPDATE, DELETE, or REPLACE path exists for verification_history rows.

---

## INV-07 — Evidence Binding

Every VERIFIED or CONTRADICTED state MUST be accompanied by evidence.

```
state in {VERIFIED, CONTRADICTED}
    ↓
evidence_id != null
evidence_id references existing evidence record
evidence.content_hash == hash(memory.content at time of verification)
```

For CONTRADICTED → VERIFIED, evidence must be post-contradiction (collected after the contradiction event).

---

## INV-08 — Provenance Completeness

Every audit record MUST contain:

```
event_type       = "memory_state_transition"
memory_id        = target memory
from_state       = state before transition
to_state         = state after transition
actor            = authenticated identity
reason           = caller-supplied reason (sanitized)
resolution       = required for CONTRADICTED → VERIFIED
evidence_ids     = list of evidence references
source           = "mcp_bridge" | "direct_fabric" | "test"
server_version   = msb-v3 version at time of transition
timestamp        = ISO-8601 UTC
request_id       = unique per request (nonce)
previous_hash    = SHA-256 of previous audit record (hash chain)
record_hash      = SHA-256 of this record (for chain verification)
```

---

## INV-09 — Defense in Depth

Security enforcement MUST exist at both:

1. **Bridge layer** (`_mf_verify`): authenticate, reject unknown actor, sanitize inputs
2. **Fabric layer** (`verify_memory`): enforce actor validity, resolution gate, transition legality

```
Bridge enforcement
    +
Fabric enforcement
    =
Defense in depth
```

If the bridge is bypassed, the fabric still enforces invariants.

---

## INV-10 — No Silent Degradation

No security check may silently pass on failure.

```
if actor check fails:
    raise (do not default to "operator")
if resolution check fails:
    raise (do not default to empty resolution)
if transition check fails:
    raise (do not silently skip)
if evidence check fails:
    raise (do not proceed without evidence)
```

Silent fallback to a permissive state is a vulnerability.

---

## PROMOTION GATE

A verification transition is GREEN only if:

```
✓ INV-01: authenticated actor present
✓ INV-02: audit identity == authenticated actor
✓ INV-03: actor is valid
✓ INV-04: resolution present when required
✓ INV-05: transition is in allowed matrix
✓ INV-06: audit record is immutable
✓ INV-07: evidence is bound
✓ INV-08: provenance is complete
✓ INV-09: both layers enforced
✓ INV-10: no silent degradation
```

Failure of any invariant = RED.

---

## ADDENDUM — same-state re-verification at the bridge boundary (2026-09-24)

No invariant above is changed by this addendum. It records how INV-05 and INV-10
apply to a request for the state a memory already holds — the case that produced
40 HTTP 500s through `/mcp` (24 `VERIFIED -> VERIFIED`, 16
`CONTRADICTED -> CONTRADICTED`) in one deployment log.

INV-05 governs *transitions*. A request for the state a memory already holds
requests no transition, so `_mf_verify` answers the postcondition instead of
attempting one:

```
POST /mcp/proxy  {tool: memory_verify, to_state: X}     memory already in X
    ↓
200  { ...item, "no_change": true }      state unchanged, no transition row
```

```
✓ INV-01/INV-02/INV-03: authentication, audit identity and actor validity all
                        run before this answer — it is not a bypass
✓ INV-05: unchanged. MemoryFabric.verify_memory still rejects X → X, still
          enforces the whole matrix (pinned by test_property_based P14-B)
✓ INV-06/INV-08: no audit row is written for a non-transition, so the trail
                 gains no self-loop the matrix forbids
✓ INV-10: not engaged. Nothing is skipped silently — the response states
          "no_change": true, and no memory reaches a state it was not in
✓ DEPRECATED: excluded from this path, so the terminal refusal stands
```

Direct fabric callers (`MemoryFabric.verify_memory`) are deliberately NOT
idempotent: the strict refusal is the fabric-layer half of INV-09.
