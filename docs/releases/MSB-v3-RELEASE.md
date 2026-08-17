# MSB v3 — Release Declaration (v0.3.0)

**Status:** RELEASED · **Dated:** 2026-08-17 · **Baseline:**
`docs/releases/v0.3.0-baseline.md` · **Tag:** `v0.3.0` (verified by
`release-verify.yml` on the self-hosted runner from a virgin clone)

## What v3 is

A narrow, local-first, governed agent runtime that takes a real task from
request to a verified, evidence-backed result — and refuses, records, and
recovers when a model, tool, or permission fails.

## What v3 is NOT

- Not a multi-user SaaS, not a remote deployment, not a dashboard product.
- Not "production-ready" by claim — readiness is earned by the evidence in
  this release (canonical run, verdict cases, failure matrix, backup/restore).
- Not a sandboxed executor (CLI provider is best-effort isolation — L9 parked).
- Not multi-modal (parked, `blocked_on: mac-mini-storage` — v4).
- Not a distributed mesh (parked in v4).

## What is frozen

- The canonical live path: `/agent/handle` → intent → task DAG → ActionGate →
  governed tools → verification → evidence spine → audit chain → replay.
- The governed tool registry behind the ActionGate (fail-closed verdicts).
- The evidence spine + append-only audit chain + replay engine.
- The factory (classify → plan → build → test → review → verify → merge) with
  the deterministic coherence scan as the always-on reviewer safety net.
- The release-gate evidence set (fixtures under `artifacts/core-loop/`,
  `artifacts/factory-dogfood/`, `artifacts/run-report-20260817.json`).
- Expansion freeze: no new subsystem without a written exception in the
  v4 parking lot.

## Implemented and supported (on the live path — evidence-linked)

| Claim | Evidence |
|---|---|
| Governed live loop (PASS/FAIL verdicts, deterministic hash) | `artifacts/core-loop/run1/`, `case-safe/` |
| Unsafe writes denied in the live path, no mutation, denial recorded | `artifacts/core-loop/case-tainted/` + `audit.json` |
| Kill switch blocks execution, no mutation | `artifacts/core-loop/case-kill/` + `audit.json` |
| Replay reconstructs state from events (successes AND failures) | `replay.json` in each fixture |
| Failure matrix: 11/11 modes + 13/13 bypass invariants, no P0/P1 | `tests/chaos/test_failure_matrix.py` (11 modes) + `tests/governance/test_bypass.py` / `tests/vesta/test_approval_bypass.py` (13 bypass) |
| M5 soak run: 60-run seeded workload through the real loop — completion 1.0, unsafe-escape 0.0, evidence 1.0, recovery 1.0, escalation 7.95%, p50 0.001s / p95 0.051s | `scripts/soak-run.py` → `artifacts/soak-report-*.json` + `tests/chaos/test_soak.py` (deterministic per seed) |
| Backup + restore verified over a corrupted runtime | launchd `com.lordwilson.msb-backup`, restore drill 2026-08-17 |
| Retrieval is semantic with honest fallback (no silent empty) | `tests/fabric/test_fabric_retrieval.py`, `tests/api/test_mcp_search.py` |
| Metrics: queries, latency, verdicts, retries, recoveries | `scripts/run-report.py` → `artifacts/run-report-20260817.json` |
| Factory dogfood: docs-only change reached MERGED | `artifacts/factory-dogfood/run7.json` |
| Intent over-grant suppressed (model can't self-grant write) | `tests/agent/test_intent.py` |
| Ledger extracted as a standalone library (P4): `msb_ledger` with zero `msb_v3` imports; `msb_v3.uac` is a thin alias shim, so all ~73 consumers + tests work unchanged | `tests/uac/test_ledger_extraction.py` (30 guards) |
| Merkle proof-of-inclusion (P4): every audit record gets a compact O(log n) receipt verified against the root committed in the signed anchor — a third party holding only the anchor + one receipt can verify a single action | `tests/uac/test_merkle.py` (23 tests) + `docs/operations/merkle-receipts.md` |
| Capability claims are auditable (M8): every claim in THIS table links to evidence that exists and matches live test counts — enforced by a gate in `make lint`, CI, and pre-push, so the declaration cannot drift from the tree | `scripts/verify-claims.py` (PASS: 13 claims, 21 evidence paths) |
| Demo is reproducible (M8): an outsider can bring MSB up from a clean checkout and run the canonical path + three verdict cases + Merkle receipt | `docs/QUICKSTART.md` |
| Governed-loop console (M8): a single-page browser console at `/console` to run a task, see the gate verdict + tool trace, replay a run from its events, and list past runs — a client of the existing operator-gated API (token entered once, kept in sessionStorage), no new backdoor | `src/msb_v3/api/console.py` + `tests/api/test_console.py` (6 tests) |

## Implemented but experimental (not on the default live path)

- Frontier/cloud model seam (`OPENAI_FRONTIER_URL/MODEL`) — configured, live
  use UNVERIFIED (opt-in `tests/live/test_frontier_smoke.py`).
- Signed-device approval (vesta) — implemented, low-volume live usage.
- MoIE engine — exists and powers the factory reviewer, not a general chat path.
- RFC 3161 timestamping + off-box notary — implemented, notary automation
  scheduled; anchor key trust boundary documented.
- Chain-anchor key — moved out of plaintext into the macOS login keychain
  (was world-readable in `.env`); Secure Enclave and YubiKey PIV backends both
  implemented, tested (hermetic, no hardware needed), and fail-closed. YubiKey
  PIV is the no-Apple-ID hardware path: key generated on-device, never leaves
  it, PKCS#11 ECDSA-SHA256 (python-pkcs11 + libykcs11). Enrollment is a one
  command (`bash scripts/yubikey-enroll.sh`) once a key is bought.
  (docs/operations/secure-enclave-anchor.md, docs/operations/yubikey-piv-anchor.md)

## Planned for v4 (parking lot — see `v4-parking-lot.md`)

Strong sandbox · full tenant isolation · multimodal · distributed mesh ·
agent factory · autonomous evolution · DB schema versioning.

## Limitations (honest, not hidden)

1. No formal DB schema versioning/migrations.
2. Factory live LLM reviewers (0.5B/8B) missed a seeded doc contradiction —
   the deterministic coherence scan is the actual catch (hermetic M4 suite
   proves the deterministic reviewer catches seeded defects).
3. CLI agent provider is best-effort isolation, not a sandbox.
4. Tenant chat routing not tenant-scoped (RAG is).
5. Deleted-file diffs not emitted by factory `compute_changes`.
6. Live p50/p95 latency currently reflects single-sample runs; the report
   mechanism is proven, the sample size grows with the 30-day trial.
