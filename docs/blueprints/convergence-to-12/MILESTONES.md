# MSB v3 — Convergence Milestones (evidence gates, not dates)

**Opened:** 2026-08-16. A milestone is COMPLETE only when its exit evidence
exists (Blueprint Rule 2). Status: 🔴 not started · 🟡 in progress · 🟢 complete.

| M | Name | Status | Exit evidence (links) | Decision memo |
|---|---|---|---|---|
| M0 | Scope Lock and Baseline | 🟡 | v3 contract ✅ [`v3-contract.md`](v3-contract.md) · surface inventory ✅ [`surface-inventory.md`](surface-inventory.md) · baseline reproducible (portability gate + CI: lint/test/hygiene/security) · freeze active (Rule 3) | — |
| M1 | Core Loop Selection | 🟢 | Canonical workflow named + one rejected alternative · state machine · golden fixtures · run-id observability — [`M1-core-loop.md`](M1-core-loop.md) + [`fixtures/handle-loop/`](fixtures/handle-loop/) | [2026-08-16: complete. Chosen: governed agent handle loop (`/agent/handle`). Rejected: daily research digest (no consequential tool actions to govern). State machine mapped from `handle.py`; boundaries (input/intent/plan/gate/execute/observe/verify/record/report-recover) documented; golden fixtures committed incl. a live PASS run (`fb0b15ed6c48aedb`).] |
| M2 | Governance in the Loop | 🔴 | Guard on live path · fail-closed denials · complete evidence · observable governance metrics · bypass regression tests | — |
| M3 | Shipping-Surface Convergence | 🔴 | No dateless shipping stubs · no misleading claims · before/after inventory · gates green | — |
| M4 | Factory Dogfood | 🔴 | Full factory run artifact · diverse reviewer real · seeded defect caught · no abandoned worktrees | — |
| M5 | Reliability & Adversarial Proof | 🔴 | Failure matrix implemented · no silent unsafe continuation · soak report · bounded recovery · security cases | — |
| M6 | Personal Production Trial | 🔴 | ≥30 days usage · measurable value vs baseline · failure burden known · release driven by operating data | — |
| M7 | Independent User Validation | 🔴 | ≥1 user completes task unaided · behavioral feedback · reproducible setup · understandable trust model | — |
| M8 | Public 12/10 Release | 🔴 | Auditable claims · reproducible demo · quantified results · prominent limitations · dated release decision | — |

## Operating ledger (claim → evidence → gap → next action)

| Claim | Evidence | Gap | Next action |
|---|---|---|---|
| Governed tool loop works end-to-end | `/agent/handle` demo: plan → execute → verify → evidence; 1288→1331 tests green | Only exercised via API/CLI, not as the M1 canonical workflow | M1: pick the canonical workflow and run it through the full spine |
| Evidence + replay reconstruct a run | Phase 2/3 tests: decision→execution→verification vertebrae, replay consistency | No independent-engineer reconstruction demonstration | M4/M8: publish a full reconstruction from evidence alone |
| Audit chain is tamper-evident | Append-only triggers, hardened repair(), anchor + off-box notary + RFC 3161, signing seam | Anchor key still on-box (documented trust boundary) | Operator: provision Secure Enclave/YubiKey |
| Factory does independent review | `ReviewPanel` builder∉reviewers invariant, fail-closed LLM experts | No real MSB change has gone through the factory yet | M4: dogfood one real change |
| Multimodal is isolated | Flag-gated 503 + both-side tests | Still stub code in the shipping tree | M3: implement narrow contract or move to experiments/ |

## Parking lot (Rule 3 — dated, with trigger)

_Empty. Add new-idea entries here during the freeze: proposed value, dependency, trigger for reconsideration._
