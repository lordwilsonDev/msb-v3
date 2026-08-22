# Independent review — msb_ledger trust code

**Date:** 2026-08-22 · **Reviewer:** independent pass (give-away readiness, item 6)
**Scope:** the trust-critical surface of `src/msb_ledger/` — `audit_chain.py`,
`merkle.py`, `signing.py`, `chain_anchor.py`, `timestamping.py`, `notary.py`.
**Method:** full read of the six modules + the existing test suite run, plus a
live verification of the production chain.

## What the ledger claims

1. **Append-only, tamper-evident history** — every record's hash covers its
   content + the previous hash; editing any past record breaks every record
   after it.
2. **Whole-DB replacement is detectable (T7)** — an external signed anchor
   (separate file) commits the chain tip; replacing the DB changes the tip.
3. **Rollback is detectable** — an off-box notary keeps signed tips the local
   attacker cannot rewrite together with the DB.
4. **Compact, independently-verifiable receipts** — Merkle proof-of-inclusion
   per record, committed in the signed anchor.
5. **Provable "when"** — optional RFC 3161 TSA timestamps on notary entries.

The honest boundary (stated in the code and in `README-OUTSIDERS.md`):
tampering is **detectable**, not impossible. Compromise of the anchor signing
key defeats the anchor; the default key is an on-box Ed25519 seed, with
Secure-Enclave / YubiKey backends available to move it off-box.

## Evidence

- **Run:** `pytest tests/uac/` → **185 passed** (merkle, chain, anchor,
  signing, timestamping, notary suites).
- **Run (production, verify-only path):** internal chain `valid` at 17,005
  records; anchor `valid`, `stale=False`, signed by the current key; anchor
  re-signed by the live server's per-append re-anchoring.
- **Read:** all six modules above, line by line.

## Verified strengths

| # | Property | Where | Notes |
|---|----------|-------|-------|
| S1 | Append-only enforced at the storage layer (UPDATE/DELETE triggers refuse) | `audit_chain._create_triggers` | `tamper()` documents the residual (drop triggers first); the anchor + notary are the real detection layer. Honest. |
| S2 | No chain fork under concurrency | `audit_chain.append` | `BEGIN IMMEDIATE` before the prev-hash read; read+insert in one write transaction. |
| S3 | Verification recomputes every hash + checks prev_hash linkage | `audit_chain.verify_chain` | A break is reported from the first broken record forward. |
| S4 | T7 closed: live tip/seq/chain-sha256/Merkle root vs signed snapshot | `chain_anchor.verify` | Distinguishes **stale** (anchored tip still present, superset) from **replacement** (tip absent). |
| S5 | Notary never trusts the local last line | `notary.verify` | Reads the REMOTE head; `DIVERGED` (local rollback), `REMOTE_BEHIND`, `REMOTE_UNREACHABLE` verdicts; append-only-per-object remote. |
| S6 | Key registry: cross-signed rotation, recovery key, revocation | `chain_anchor.KeyRegistry` | New anchors require current/recovery key; notary accepts any ever-registered key (rotation-safe history). |
| S7 | No anchor clobbering | `AnchoredAuditChain.__init__` | Refuses an existing anchor signed by an unauthorized key (found live, fixed). |
| S8 | Merkle domain separation + CT binding | `merkle.py`, `audit_chain.verify_inclusion` | 0x00/0x01 prefixes; receipt bound to the live record's current hash. |
| S9 | RFC 3161 validation is genuinely cryptographic | `timestamping._validate` | messageImprint match, nonce echo, signedAttrs messageDigest + contentType, CMS signature over the RFC 5652 SET-OF re-wrap, genTime within cert validity. Fail-closed. |
| S10 | Repair is governed | `audit_chain.repair` | Operator token (constant-time), notary cross-check before rewrite, audited repair event in the same transaction. |
| S11 | Fail-closed wiring at execution boundaries | `verify_trustworthy` | Used by Vesta write/shell, repair, auto-repair — a tampered chain refuses consequential action. |

## Findings (severity-ranked)

**F1 — TSA signer certificate is not CA-trusted or pinned (Medium).**
`timestamping._validate` verifies the token's signature and the signer cert's
validity window, but never validates the signer certificate against a CA
chain or a pinned key. An active network MITM on the TSA connection could
present their own self-signed cert with a valid signature; the nonce and
messageImprint are echoed from the request, which the MITM sees. Impact: the
*provable when* is weakened under active MITM — the anchor signature (the
actual integrity guarantee) is unaffected. Recommendation: pin the TSA
certificate/CA, or document the boundary explicitly. Low urgency; the
timestamp is a secondary attestation.

**F2 — A valid-but-stale anchor reports `valid: True, stale: True` (Low).**
Callers using `verify_trustworthy` treat a stale anchor as trustworthy. The
un-anchored tail has no T7 protection. In normal operation the wrapper
re-anchors per append, so this is a degraded-mode signal, and it IS reported
— but any consumer gating a consequential action should also check `stale`.
Recommendation: keep the semantics (the anchor is valid for what it covers);
make the stale flag a first-class check in `verify_trustworthy`.

**F3 — Staleness detection fails open on unparseable timestamps (Low).**
`_chain_newer_than_anchor` returns 0.0 ("covered") on a `ValueError`, so a
corrupted timestamp would under-report staleness. Timestamps are written by
`_now_iso()`, so this is defensive-only. Recommendation: fail closed (treat
unparseable as stale).

**F4 — A corrupt key-registry file raises instead of returning invalid (Low).**
`KeyRegistry.load` does `json.loads` + `bytes.fromhex` with no guard; a
truncated/tampered registry makes `ChainAnchor.verify` raise rather than
return a clean invalid verdict. It fails loudly (which is fail-closed in
effect) but not gracefully. Recommendation: wrap registry load in verify and
return `{"valid": False, "reason": "registry unreadable"}`.

**F5 — Append commits before re-anchor; a failed re-anchor leaves a stale
anchor (Low).** `AnchoredAuditChain.append` commits the record, then
re-anchors; if the re-anchor write fails (e.g., disk full) the record is
committed, the anchor is stale, and the caller sees an exception. The chain
stays internally valid and the next verify reports stale. Recommendation:
document; optionally roll back the append if the re-anchor fails.

**F6 — Default anchor key is on-box (By design, must be known).** The
software Ed25519 seed lives in env/file/keychain. The code states this
boundary honestly, and hardware backends fail closed until provisioned. This
is the single most important thing a stranger must know before relying on the
ledger — now stated in `README-OUTSIDERS.md`.

**F7 — Hash canonicalization is Python-json, not RFC 8785 JCS (Info,
documented).** `_compute_hash` uses `json.dumps(sort_keys=True,
ensure_ascii=False)`; non-finite floats are rejected. Self-consistent for
JSON-native types; cross-tool canonical hashing is not guaranteed. A JCS
migration is deferred and documented (it would change every existing hash).

**F8 — Merkle root/proof load the whole chain into memory (Info).** O(n) for
large chains; a scalability note, not a security issue.

## Verdict

**The ledger is safe to rely on for its documented claim** — *tampering is
detectable*: edits break the chain, whole-DB replacement breaks the anchor,
rollback breaks the notary — **provided the operator** (a) keeps the anchor
key out of the attacker's reach (ideally Secure Enclave / YubiKey),
(b) keeps the notary remote genuinely off-box, and (c) treats the
software-default key as the documented boundary.

No finding defeats the core guarantee. F1–F5 are hardening items, not
blockers; F1 (TSA cert trust) is the one worth an explicit operator decision.
The suite (185 tests) pins the important behaviors, and the production chain
verifies valid and freshly anchored.

**Recommendation before calling it "safe to rely on" in a stranger's hands:**
resolve F1 (pin/validate the TSA cert or state the boundary) and F3/F4
(fail-closed hardening) in a follow-up; the rest can be documented as
accepted.
