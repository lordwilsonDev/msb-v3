# MSB Cockpit Verification Matrix v1

**Source:** `config/cockpit_verification_manifest.yaml`  
**Plan:** `docs/audits/msb-cockpit-build-plan-v1-2026-09-24.md`  
**Interrogation source:** `docs/audits/msb-cockpit-build-verification-interrogation-2026-09-24.md`  
**Date:** 2026-09-24

## How to use this matrix

The YAML manifest is the machine-readable source of truth. This document explains the gate groups and the evidence required to move a gate from planned to confirmed.

A gate is not complete because a module exists. It is complete only when:

1. the mechanical control exists;
2. a focused test exercises the control;
3. the test result is captured;
4. an independent verifier or isolated environment checks the result where required;
5. the evidence location and limitations are recorded;
6. the release/claim layer does not overstate the result.

## Status vocabulary

| Status | Meaning |
|---|---|
| `confirmed_in_code` | Direct implementation evidence exists, but this is not automatically physical-world proof. |
| `partially_evidenced` | A real control exists, but one or more required cases remain open. |
| `planned` | The requirement is in scope and has a defined implementation path, but the control is not complete. |
| `physical_test_required` | Cannot be closed by ordinary unit tests alone. |
| `unknown` | The required fact has not been collected or independently checked. |
| `blocked_external_dependency` | A second host, media, power harness, credential, or operator is required. |
| `not_yet_proven` | The target property exists as a goal but no complete evidence package exists. |

## Gate groups

| Gates | Area | Priority | Current state | Mechanical control to close | Required evidence |
|---:|---|---|---|---|---|
| 1–14 | Mac mini M1 hardware baseline | P0–P3 | Mostly unknown/physical | Hardware manifest, runtime inventory, resource policy | Production host capture, hardware baseline, 8/16 GB pressure run |
| 15–18 | Original starvation and scope | P0–P1 | Partially evidenced | Bounded verification, causal dependency graph, isolation contract | Request starvation regression and code-path trace |
| 19–32 | Append-only audit and external anchor | P0 | Partially evidenced | Canonical chain, append-only storage, signed checkpoints, remote notary | Independent verifier, whole-store rollback, restore acceptance, media rehearsal |
| 33–40 | SQLite durability and storage failure | P0 | Planned/partial | Shared connection contract, WAL/synchronous settings, storage state machine | Power loss, disk-full, corruption, migration, queue-depth tests |
| 41–43 | Backup and replacement-host recovery | P0 | Planned/blocked | Quarantine restore, external restore procedure, second-host gate | Actual restore on second Apple Silicon host |
| 44–50 | Resource containment and test isolation | P0–P1 | Planned | Bounded executor, admission control, read-only verifier identity, separate volumes | 30-minute load, 8 GB test, test access-control evidence |
| 51–55 | Replay, schema, UI, authority | P0–P1 | Partial/planned | Versioned schema, replay fixtures, authenticated operator actions | Replay regression, localhost authorization test, role separation test |
| 56–66 | launchd, Qdrant, dependencies, memory | P0–P1 | Partial/planned | Native supervisor, image/page contract, post-restart vector gate, retention | Apple Silicon Qdrant restart, Docker failure, memory retention tests |
| 67–74 | Storage growth, archival, physical failure | P0–P2 | Planned/physical | Exact storage state machine, archival, environment manifests | Low-space, rotation, replacement, and manifest reconstruction evidence |
| 75–78 | Independence, privacy, 2036 | P0 | Partial/not proven | Off-box anchor, second verifier, final assurance package | Replacement-host verification and 2036 rehearsal |
| 79–82 | Formal invariants | P0–P1 | Planned | Invariant registry, memory/storage alerts, Qdrant health gate | Invariant traceability report and failure tests |
| 83–89 | Adversarial matrix | P0–P1 | Physical/planned | Test manifest and isolated harnesses | Every row has PASS, FAIL, or BLOCKED evidence |
| 90–93 | Priority and non-goals | P0–P1 | Partially classified | Manifest priority and non-goal review | CI classification and architecture review |
| 94–101 | Recursive controller and physical truth | P0–P1 | Planned/not proven | Role boundaries, freshness, test threat model, final package | Static role audit and independent reconstruction |
| 102–104 | Deliverable status gate | P0–P1 | Partial | Traceability and claim-evidence policy | Documentation audit and release-claim scan |

## P0 closure order

The implementation sequence is intentionally dependency-driven:

```text
Gate manifest
  → canonical schema
  → independent verifier
  → SQLite durability/storage state
  → external checkpoint and restore acceptance
  → test/production isolation
  → second-host restore
  → resource containment
  → Cockpit UI and control plane
```

A green UI cannot promote the project ahead of an unverified audit or restore path. The UI is explicitly downstream of the data plane.

## Current confirmed implementation anchors

These are implementation anchors, not complete gate closures:

- `src/msb_ledger/audit_chain.py` already contains a hash chain, append-only SQLite triggers, transaction-scoped sequence allocation, and chain verification.
- `src/msb_ledger/chain_anchor.py` already contains signed anchor and fail-closed keyless-append concepts.
- `src/msb_ledger/notary.py` already contains local/remote append-per-object notarization and remote-head verification.
- `src/msb_ledger/merkle.py` already contains inclusion proofs.
- `src/msb_v3/infrastructure/qdrant_contract.py` already contains a Qdrant preflight contract.
- `scripts/` contains existing LaunchAgent templates and operational scripts.

The remaining work is to unify these into one enforced contract, close the failure cases, and produce independent evidence.

## Required artifact classes

Every implementation phase must produce at least one of these artifact classes:

| Artifact class | Purpose |
|---|---|
| `spec` | What the system must do before code changes. |
| `code` | The implementation, with no undocumented behavior. |
| `unit_test` | Deterministic function-level behavior. |
| `integration_test` | Component interaction. |
| `adversarial_test` | Attack or failure behavior. |
| `physical_test` | Real hardware, power, media, or OS behavior. |
| `independent_evidence` | Verification by a separate identity/process/host. |
| `runbook` | Operator procedure and rollback. |
| `claim_record` | What may be stated publicly or operationally. |

## Physical gates that cannot be closed from code alone

These are deliberately called out rather than hidden inside “test later”:

- target Mac mini hardware inventory;
- 8 GB and 16 GB memory behavior;
- Apple NVMe abrupt-power-loss behavior;
- actual 16 kB-page Qdrant container behavior;
- thermal behavior under sustained 4P+4E load;
- external Thunderbolt/WORM checkpoint medium;
- second Apple Silicon restore;
- macOS major-version migration;
- SSD/logic-board/FileVault failure recovery.

## Closure rule

A gate may move to `confirmed_in_code` only when its local implementation and tests pass. A gate may move to a final assurance status only when its physical and independent evidence requirements are also satisfied. The manifest must never collapse those two meanings.
