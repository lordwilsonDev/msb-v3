# ADR-003: Evidence Spine — Hash-Chained Decision Records

**Status**: Accepted  
**Date**: 2026-08-25  
**Decision Maker**: Lord Wilson  

## Context

Every governed action produces evidence. Without a tamper-evident record, evidence can be altered after the fact, undermining the entire governance model.

## Decision

Implement an append-only hash-chained evidence spine:
- Every record contains: `seq`, `parent_hash`, `content_hash`, `payload`
- Each `content_hash` covers the previous record's hash + the current payload
- `verify_chain()` walks the full chain, recomputing hashes
- Tampering with any record breaks the chain
- The chain starts from a genesis record and is monotonically increasing

## Consequences

### Positive
- Tamper-evident: any modification to historical records is detectable
- Simple: hash chains are well-understood and provably secure
- Auditable: chain can be verified by any party with read access
- Dual chains: evidence spine (decision-level) + calibration store (prediction-level)

### Negative
- Append-only: records cannot be updated, only new records added
- Chain length grows unboundedly (mitigated by periodic anchoring)
- Verification is O(n) — linear in chain length

### Risks
- Disk growth if evidence is not archived
- Genesis record corruption breaks the entire chain

## References
- 93 records in evidence spine at decision time
- 4 records in calibration store at decision time
- Both chains verified intact post-Phase 7
