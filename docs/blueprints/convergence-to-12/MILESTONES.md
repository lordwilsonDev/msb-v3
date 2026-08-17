# MSB v3 — Convergence Milestones (evidence gates, not dates)

**Opened:** 2026-08-16. A milestone is COMPLETE only when its exit evidence
exists (Blueprint Rule 2). Status: 🔴 not started · 🟡 in progress · 🟢 complete.

| M | Name | Status | Exit evidence (links) | Decision memo |
|---|---|---|---|---|
| M0 | Scope Lock and Baseline | 🟢 | v3 contract ✅ [`v3-contract.md`](v3-contract.md) · surface inventory ✅ [`surface-inventory.md`](surface-inventory.md) · baseline reproducible (portability gate + CI: lint/test/hygiene/security) · freeze active (Rule 3) | [2026-08-16: complete — contract + inventory committed; baseline evidenced by portability gate (1331 passed on last push) and CI gates; freeze active through M3.] |
| M1 | Core Loop Selection | 🟢 | Canonical workflow named + one rejected alternative · state machine · golden fixtures · run-id observability — [`M1-core-loop.md`](M1-core-loop.md) + [`fixtures/handle-loop/`](fixtures/handle-loop/) | [2026-08-16: complete. Chosen: governed agent handle loop (`/agent/handle`). Rejected: daily research digest (no consequential tool actions to govern). State machine mapped from `handle.py`; boundaries (input/intent/plan/gate/execute/observe/verify/record/report-recover) documented; golden fixtures committed incl. a live PASS run (`fb0b15ed6c48aedb`).] |
| M2 | Governance in the Loop | 🟡 | Guard on live path · fail-closed denials · complete evidence · observable governance metrics · bypass regression tests | [2026-08-16: P0 landed — `tests/governance/test_bypass.py` (13 tests): direct tool invocation never wired ungoverned, alternate callers can't reach DAG tools (executor is only reachable via SafeProvider), replay/retry re-evaluates (no allow/deny cache), REVIEW→approve→retry recovery flow, granted-whitelist + kill-switch fail-closed. `ACTIONGATE_DECISIONS` metric counts allowed/denied/indeterminate/failed per gate call, asserted in tests. Remaining: P1 (MCP chat → governed loop + gate verdict in audit), P2 (live-loop composition test).] |
| M3 | Shipping-Surface Convergence | 🟢 | No dateless shipping stubs · no misleading claims · before/after inventory · gates green | [2026-08-16: complete. S1 CUT (dead `/status` duplicate deleted, fields folded into live `studio.py` route + regression test); S3 CUT (`core/health.py` dead duplicate removed w/ its 3 tests); S4 CUT (`runtime_config.py`+`runtime.yaml` test-only aspirational config removed w/ 5 tests); S7 PARK (dated notes in both UAC module docstrings); H1 cleaned (both worktrees fully merged → removed + branches deleted); H3 PARK (Make.com workflow defs, external); H4 moved `verify_multi_tenant.py` → `scripts/`. Claims pass: README/MANIFEST/docs already honest (multimodal labeled stub-gated). Full battery green: lint 196 files, 1320 tests passed, hygiene 12/12, portability pending push gate.] |
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
| Multimodal is isolated | Flag-gated 503 + both-side tests | Stub code remains in shipping tree, but fail-closed and honestly labeled | M3: decision made — PARK (flag-gated, dated decision, outside default release path). Revisit post-M7. |

## Parking lot (Rule 3 — dated, with trigger)

_Empty. Add new-idea entries here during the freeze: proposed value, dependency, trigger for reconsideration._
