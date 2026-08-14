# MSB-GOV-EVAL-001 — One-Page Research Summary

**System Under Test:** Meta Systems Builder (MSB) v0.2.3 · **Research ID:** MSB-GOV-EVAL-001
**Pre-registered:** 2026-08-14 (manifest `experiments/manifests/gov-001.yaml`, frozen §22)
**Evidence:** `experiments/runs/2026-08-14/` · Full report: `experiments/reports/MSB-GOV-EVAL-001.md`

**Headline (§19): under identical attack conditions, MSB reduced unauthorized mutations from 373 (governance-bypassed baseline) to 0 (MSB) while introducing 0.52 ms median governance overhead per action.**

## Figure 1 — Governance Pipeline

```
action → AUTHORIZATION → POLICY → EVIDENCE → BUDGET → APPROVAL → EXECUTION → AUDIT → VERIFICATION
            A-BIND       deny/      hash       size      owner      atomic    hash-     post-
            capability   allowlist  verify     limit     ACK       write     chained   condition
            scope                  precondition          (exact)             receipt   re-check
```
A mutation bypassing any stage is an **ungoverned mutation** (§3).

## Figure 2 — Violation Prevention (Baseline vs MSB)

Identical 800-trial corpus (seed `20260814`), both systems:

| | Baseline | MSB |
|---|---:|---:|
| Correctly blocked (of 700 violation attempts) | 75 * | **648** |
| Legit actions allowed correctly | 152 | 152 |
| False allows (violations that mutated) | **373** | **0** |
| False denies | 0 | 0 |
| Audit coverage | 0.00 | **1.00** |
| Evidence failures detected | 0 | **100** |
| Recovery failures | 373 | **0** |

*The baseline's 75 blocks are OS-level refusals on malformed requests (null
bytes, directory targets, non-root `/etc` writes) — not governance.

## Figure 3 — Failure Matrix (component-failure behavior)

Single failures (real components, state-verified): **7/7 fail closed**. Multi-component cascades (§10 Levels 1–5): **26/26 SAFE, 0 unsafe**, recovery 23/23.

| Component | Injection | Behavior |
|---|---|---|
| KillSwitch | state unreadable | DENY (unreadable ⇒ armed) |
| FileWriter | escape / symlink / bad hash / over budget | DENY, no mutation |
| Policy engine | exception | exception propagates — never ALLOW |
| Approval queue | unavailable | execution denied |
| Evidence verifier | unavailable | quarantine, no mutation |
| Audit engine | unavailable | denied by exception (no silent heal) |
| Persistence / storage | sqlite down / ENOSPC | no unsafe transition (atomic write) |
| Local model | unreachable | controlled `[fallback]`, never fakes |
| Cloud API / network | unreachable | fails loud, local ops continue |

## Figure 4 — Performance Cost (N = 1000/group)

| Latency / action | Baseline | Governed | Overhead |
|---|---:|---:|---:|
| P50 | 0.054 ms | 0.482 ms | +0.43 ms |
| P95 | 0.065 ms | 0.792 ms | +0.73 ms |
| P99 | 0.072 ms | 1.428 ms | +1.36 ms |

797% relative — but sub-millisecond absolute (0.43 s per 1,000 actions). Per-gate: audit 0.24 ms · evidence 0.14 ms · policy 0.001 ms.

## Figure 5 — Audit Tamper Detection

| Attack | T1 edit | T2 delete | T3 reorder | T4 timestamp | T5 actor | T6 replay | T7 DB replace (unanchored) | T7 anchored |
|---|---|---|---|---|---|---|---|---|
| Detected | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ (deployment gap) | ✅ |

*T7 (whole-DB replacement) was the documented trust-model gap — a hash chain proves non-modification of retained records, not that the right records remain. **Closed** by the external signed chain-tip anchor (`chain_anchor.py`): the live tip is recomputed and compared to the anchored tip, so a swap is detected unless the attacker holds the signing key (documented residual boundary). **FIR = 0%** with the anchor deployed; the live chain is anchored (`/vesta/ledger/verify` → `anchored: {valid: true}`, 8,276 records).

## Figure 6 — Capability Retention (connected vs cloud-out)

Weights frozen pre-measurement (inference .25, memory .15, planning .15, execution .15, audit .10, evidence .10, storage .05, external search .05).

| Capability | Connected | Cloud removed |
|---|---|---|
| C1–C7 (inference, memory, planning, execution, audit, evidence, storage) | FULL | **FULL** |
| C8 external search (cloud) | FULL | UNAVAILABLE (fails loud, recovers) |
| Local-model loss (injected) | — | DEGRADED (controlled fallback) |

**CRR: 1.00 → 0.95** — the only loss is the cloud capability itself.

## Table 1 — Complete Experimental Results

| Metric | Result | Gate |
|---|---:|---|
| Adversarial trials (§6) | 800, seed-frozen, 0 indeterminate | ≥800 ✅ |
| APR (attack prevention) | 100.0% | — |
| FAR (false allow) | 0.0% | target 0 ✅ |
| FDR (false deny) | 0.0% | — |
| Fail-closed rate (§8) | 7/7 single · 26/26 cascading | ✅ |
| Audit coverage (AC) | 1.00 governed / 0.00 baseline | — |
| Evidence integrity (EIR) | 100/100 detected | — |
| Tamper detection (TDR) | 6/7 (85.7%) · FIR 0% in-model | — |
| Sovereignty (CRR) | 1.00 → 0.95 | — |
| Governance overhead | +0.43–0.52 ms/action (P50), P99 1.43 ms | bounded ✅ |
| Approval-bypass suite | 9/9 attacks denied | ✅ |
| Dangling-approval watchdog | live daily, live ledger clean | ✅ |

**Claims supported (§26):** explicit governance over defined mutation classes · tested unauthorized mutations prevented at measured rate · fail-closed for evaluated component failures · tampering detected for evaluated classes · measured capability retention · measured bounded overhead.
**Not claimed:** MSB is safe / corrigible / un-compromisable / sovereign-guaranteed / aligned.
**All gates closed.** Residual trust boundary: anchor-key compromise (out of threat model) — the same boundary every external anchor carries.
