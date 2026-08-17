# MSB v3 — Architecture Overview (public)

One page for a technically capable reader: what the system is shaped like,
how a request flows, and where the guarantees come from. This is the
"architecture diagram" artifact of the v0.3.0 release; the implementation
contracts live in `docs/blueprints/` and the code itself is the source of
truth.

## Shape

MSB v3 is a **local-first, governed agent runtime**: a FastAPI server
(`:8766`) with SQLite persistence, local models via Ollama, and an
append-only audit ledger. There is no cloud dependency for the core path —
everything below runs on one machine.

```
                 ┌─────────────────────────────┐
                 │         OPERATOR            │  human / device auth
                 │   (bearer token / signed)   │
                 └──────────────┬──────────────┘
                                ▼
                 ┌─────────────────────────────┐
                 │     REQUEST → /agent/handle  │  canonical live path
                 │  intent → task DAG → plan    │
                 └──────────────┬──────────────┘
                                ▼
                 ┌─────────────────────────────┐
                 │        ACTION GATE          │  SAFE / REVIEW / BLOCK
                 │  capability tier + taint    │  fail-closed, no cache
                 └──────────────┬──────────────┘
                                ▼
                 ┌─────────────────────────────┐
                 │     GOVERNED TOOLS          │  read/write/search/chat
                 │  (registry, not free code)  │
                 └──────────────┬──────────────┘
                                ▼
                 ┌─────────────────────────────┐
                 │        VERIFY               │  grounded checks, not
                 │  (per-tool verification)    │  "the model said so"
                 └──────────────┬──────────────┘
                                ▼
        ┌───────────────────────┴───────────────────────┐
        ▼                                               ▼
┌──────────────────┐                          ┌──────────────────┐
│  EVIDENCE SPINE  │                          │  AUDIT LEDGER    │
│  decision/action │                          │  hash chain +    │
│  /result/verify  │                          │  signed anchor   │
└──────────────────┘                          │  + Merkle receipts│
                                              └──────────────────┘
        ┌───────────────────────┬───────────────────────┐
        ▼                       ▼                       ▼
┌──────────────┐       ┌──────────────┐        ┌──────────────┐
│   REPLAY     │       │  RECOVERY    │        │  NOTARY      │
│ event-sourced│       │ bounded retry│        │ off-box,     │
│ state rebuild│       │ + quarantine │        │ RFC 3161     │
└──────────────┘       └──────────────┘        └──────────────┘
```

## The canonical path (frozen)

```
Request → classify → plan → authorize → execute → observe → verify → record → report | recover
```

Every step writes evidence; every consequential action passes the ActionGate
before execution; failures land in a defined terminal state (never silent
continuation); and the whole run is replayable from its event log.

## Where the guarantees come from

| Guarantee | Mechanism | Evidence |
|---|---|---|
| No unauthorized action | ActionGate: capability tier × provenance taint; fail-closed; BLOCK/REVIEW never executes | `tests/governance/test_bypass.py` (13 bypass invariants) |
| No unverified result | per-tool grounded verification (search returned hits, file written, synthesis non-empty) | `tests/chaos/test_failure_matrix.py` (11 modes) |
| Tamper-evident history | SQLite append-only triggers + hash chain + external signed anchor | `tests/uac/test_audit_chain.py` |
| Verifiable single action | Merkle proof-of-inclusion; receipt verified against the anchor-committed root | `tests/uac/test_merkle.py` |
| Recovery is bounded | retry policies, timeouts, quarantine, replay | `scripts/soak-run.py` (unsafe-escape 0, recovery 1.0) |
| Docs match runtime | claims-audit gate in `make lint`/CI/pre-push | `scripts/verify-claims.py` |

## The ledger is standalone

The auditable ledger (hash chain, signed anchor, off-box notary, RFC 3161
timestamps, Secure Enclave / YubiKey / keychain signing, key rotation,
Merkle receipts) lives in `src/msb_ledger/` as a **zero-coupling library**
— no imports from `msb_v3`, host components injected through structural
protocols. It is the most-tested, most-differentiated asset in the project
and can ship or be published independently.

## Not in v3

Strong sandbox · full tenant isolation · multimodal · distributed mesh ·
agent factory · autonomous evolution — parked with dated decisions in
[docs/blueprints/convergence-to-12/v4-parking-lot.md](blueprints/convergence-to-12/v4-parking-lot.md).

## Start here

- [docs/QUICKSTART.md](QUICKSTART.md) — run it yourself
- [docs/releases/MSB-v3-RELEASE.md](releases/MSB-v3-RELEASE.md) — the contract and limitations
- [docs/glossary.md](glossary.md) — terms
