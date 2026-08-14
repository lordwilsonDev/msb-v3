# VESTA-NODE × MSB v3 — Full Build Plan

**Version:** 1.0  
**Date:** 2026-08-13  
**Status:** Approved implementation plan  
**Target:** Apple Silicon Mac Mini + iPhone + MSB v3  
**Principle:** `Human → Identity → Intent → Policy → Intelligence → Capability → Execution → Verification → Evidence → Audit → Recovery`

## 1. Purpose and completion boundary

This plan completes the supplied Vesta blueprint without creating a second MSB runtime.

```text
VESTA = identity, authority, policy, capability gates, evidence, recovery
MSB   = reasoning, orchestration, memory, model routing
```

The model remains an untrusted proposer. Vesta remains the authority. No capability is considered complete until it has policy, scope, contract, budget, timeout, preconditions, postconditions, rollback or explicit irreversibility approval, audit events, kill behavior, quarantine behavior, and adversarial/recovery tests.

### Already implemented and retained

- Vesta Phase 0–2 observation/A-BIND/audit slice under `src/msb_v3/vesta/`
- Existing Sovereign Node signed P-256 enrollment/session gateway under `src/msb_v3/node/`
- Existing MSB planner, DAG, executor, verifier, governance brakes, kill switch, approval queue, memory, model routing, and `AuditChain`
- Swift protocol package under `apps/iphone/`
- Native MSB `/chat` remains unchanged

### Not yet complete

- Production hardware-backed key storage and enrollment UX
- WireGuard deployment and interface-bound ingress
- Full A-BIND task lifecycle and capability-token enforcement
- Evidence registry/witnesses and provenance metadata
- TC001/ET140 adapters and sensor conflict handling
- Claim-state semantic firewall and external-transmission gate
- Signed human acknowledgements and P0 escalation
- Safe mutation, shell, browser, GUI, and voice capabilities
- Sandbox/process isolation, signed runtime admission, backup/restore, and production operations

## 2. Decisions fixed for this build

1. **Transport:** self-hosted WireGuard on a private overlay. The Vesta gateway binds to the tunnel interface; no executor port is public. No managed auth/network service is a runtime dependency.
2. **Identity:** iPhone private keys use Secure Enclave/Keychain. Mac identity uses the strongest available Apple hardware-backed mechanism through a small native helper; Python never receives the private key. Unsupported hardware is recorded as lower assurance, never reported as hardware-attested.
3. **Signature verification:** replace the dependency-free prototype verifier with an approved audited cryptographic provider after a compatibility fixture is established. The protocol remains versioned P-256 until an ADR changes it.
4. **Enrollment:** one-time local QR/manual pairing with a short-lived pairing code; only one owner device in V1. Re-enrollment requires local operator action. Additional devices are read-only until a multi-owner policy exists.
5. **State:** SQLite remains the state store; the existing hash-chain `AuditChain` remains the ledger. No second memory graph or parallel audit implementation will be built.
6. **Evidence:** content-addressed local evidence files plus SQLite metadata; SHA-256 hashes are recorded in the audit chain. V1 claims tamper-evident local evidence, not external WORM immutability.
7. **Initial roots:** `runtime/node-sandbox/` for development and an explicitly configured allowlist for production. Home-directory and unrestricted filesystem access are forbidden.
8. **Execution order:** read-only → create file → controlled modification → approved shell → browser/app control → GUI/physical agency → voice.
9. **Approval:** high-risk actions require a signed owner ACK bound to the exact request, target, capability set, contract, policy version, and expiration.
10. **Sensors:** adapters are read-only first. A sensor disagreement produces `CONFLICT` or `INSUFFICIENT`, never automatic certainty.

## 3. Repository shape

Extend the existing packages rather than creating a parallel `vesta/` top-level runtime:

```text
src/msb_v3/
├── vesta/
│   ├── api.py                 # trust-boundary API
│   ├── models.py              # A-BIND, policy, evidence, claim contracts
│   ├── policy.py              # deterministic authorization
│   ├── adapter.py             # MSB boundary
│   ├── identity/              # provider-backed device identity
│   ├── transport/             # session/replay/tunnel admission
│   ├── runtime/               # task lifecycle adapter
│   ├── capabilities/          # filesystem/shell/browser/GUI providers
│   ├── evidence/              # witness, registry, manifests
│   ├── claims/                # semantic firewall
│   ├── sensors/               # TC001, ET140, quorum
│   ├── recovery/              # rollback and quarantine
│   └── escalation/            # ACK/P0 state machines
├── node/                      # existing signed node gateway; reuse, then converge boundaries
├── agent/                     # existing MSB planner/DAG/executor
├── governance/                # existing brakes, budgets, approvals, kill switch
└── uac/audit_chain.py         # existing shared ledger

apps/iphone/
├── Package.swift              # protocol package already present
└── SovereignNodeApp/          # real iOS SwiftUI target in Phase 7

tests/
├── vesta/                     # policy, A-BIND, evidence, claims, API
├── node/                      # identity, session, capability, gateway
├── adversarial/               # escalation and bypass attempts
└── recovery/                  # crash, rollback, quarantine, restart
```

The exact final package split may be adjusted during implementation, but no new package may duplicate MSB orchestration, memory, or the existing audit chain.

## 4. Phase plan and exit gates

### Phase A — Contract and threat freeze

**Goal:** make the entire system testable before adding authority.

**Build:**

- ADRs for trust boundaries, protocol canonicalization, capability taxonomy, risk matrix, state machine, evidence schema, and operator recovery.
- Versioned contracts for `ABind`, `CapabilityGrant`, `ActionContract`, `EvidenceObject`, `Claim`, `PolicyDecision`, `Approval`, `ExecutionReceipt`, `QuarantineRecord`, and `TrustManifest`.
- Threat-to-test matrix covering replay, forgery, scope expansion, prompt/model manipulation, approval replay, audit tampering, partial mutation, resource exhaustion, and GUI state drift.
- Explicit deadlines, clock skew, request limits, budget defaults, retention, and failure behavior.

**Exit gate:** every capability has a scope, risk, approval rule, evidence requirement, verification method, rollback strategy, and test owner. No model-generated field can set policy authority.

### Phase B — Hardware identity and enrollment

**Goal:** establish who is allowed to speak for the node.

**Build:**

- Native Mac key helper and iOS Secure Enclave/Keychain key storage.
- `NodeIdentity` with node ID, public key, key version, assurance level, device binding, and status: `ACTIVE`, `LOCKED`, `QUARANTINED`, `REVOKED`.
- One-time enrollment ceremony with QR/manual public-key exchange and local operator confirmation.
- Key rotation, revocation, device lock, and quarantine persistence.
- No private-key export endpoint and no secret in logs/audit payloads.

**Tests:** key non-export, challenge signing, invalid key, revoked key, rotation, restart persistence, lower-assurance fallback.

**Exit gate:** the Mac verifies an enrolled iPhone signature; revoked/locked/quarantined devices cannot create controlled sessions or mutations.

### Phase C — Private transport and authenticated sessions

**Goal:** make the gateway reachable only over the private authenticated path.

**Build:**

- Deploy WireGuard on the Mac Mini and iPhone profile.
- Bind Vesta ingress to the WireGuard interface and local loopback; firewall all other ingress.
- Keep `MSB_OPERATOR_TOKEN` as maintenance auth only; do not use it as phone identity.
- Complete challenge/session endpoints with versioned signatures, session expiry, timestamp skew, request IDs, nonces, and durable replay state.
- Add session revocation and device revocation propagation.
- Add a transport admission check to Vesta before A-BIND creation.

**Tests:** public-interface denial, valid tunnel request, invalid signature, stale timestamp, nonce reuse, duplicate request ID, expired session, revoked device, restart during handshake.

**Exit gate:** the raw MSB runtime and executor are unreachable from the public interface; only a valid enrolled session can enter the controlled boundary.

### Phase D — Full A-BIND and policy kernel

**Goal:** turn the current chat binding into a complete authority context.

**Build:**

- Extend A-BIND with parent task, actor, source modality, capabilities, risk, deadline, cancellation ID, policy version, contract ID, evidence requirements, and budget.
- Implement deterministic policy decisions: `ALLOW`, `ALLOW_WITH_LIMITS`, `REQUIRE_APPROVAL`, `DENY`, `QUARANTINE`.
- Reuse existing MSB approval queue, kill switch, budgets, and governance audit hooks through adapters.
- Reject model-supplied risk, approval, target, or capability escalation.
- Revalidate policy and capability grant immediately before each operation.
- Bind approvals to exact request, target, arguments, scope, policy version, expiration, and contract hash.

**Tests:** policy determinism, unknown capability, capability substitution, expired grant, approval replay, kill precedence, quarantine precedence, budget exhaustion, cancellation.

**Exit gate:** no controlled action can execute without a valid A-BIND, policy decision, active grant, and unexpired contract.

### Phase E — Runtime lifecycle and MSB adapter

**Goal:** let MSB reason and plan without granting it direct actuator authority.

**Build:**

- Adapt existing MSB DAG planner/executor/verifier behind Vesta runtime APIs.
- Persist lifecycle:
  `RECEIVED → AUTHENTICATED → PLANNED → AUTHORIZED → APPROVED → EXECUTING → VERIFYING → COMPLETED`.
- Failure transitions:
  `VERIFYING → RECOVERING → COMPLETED|QUARANTINED`.
- Persist request/task/bind/grant/contract relationships in SQLite.
- Propagate A-BIND to every child task; children cannot gain capabilities.
- Prevent retries after expiration, cancellation, kill, quarantine, or approval mismatch.
- Keep native `/chat` and current MSB runtime behavior intact during migration.

**Tests:** DAG propagation, cycle rejection, restart at every state, parent failure, timeout, retry budget, cancellation race, model timeout, adapter failure.

**Exit gate:** one read-only task completes through the full lifecycle; restart and crash recovery are deterministic and audited.

### Phase F — Evidence witness and provenance

**Goal:** make consequential operations reconstructable.

**Build:**

- `EvidenceObject`: evidence ID, type, source, captured time, content hash, metadata hash, retention, provenance parents, and verification state.
- Content-addressed evidence store under a configured Vesta runtime root.
- Witness hooks before action, after action, after verification, and during rollback.
- Link evidence references to policy decisions, contracts, receipts, claims, and audit events.
- Add manifest export for a task: inputs, model/backend, policy version, capability, target, outputs, evidence hashes, and verification result.
- Explicitly separate local tamper evidence from external immutability.

**Tests:** duplicate evidence, corrupted blob, missing metadata, hash mismatch, provenance fork, retention failure, evidence-write failure, audit-write failure.

**Exit gate:** a completed task can be independently reconstructed from its signed request, policy decision, execution receipt, evidence hashes, and ledger chain.

### Phase G — Safe filesystem capabilities and recovery

**Goal:** introduce the first real mutation without opening the host.

**Build order:**

1. `FILE_READ` within configured roots.
2. New-file `FILE_WRITE` with atomic write and verification.
3. Controlled modification with snapshot/rollback.

**Controls:**

- Canonical path and symlink resolution.
- Root, extension, size, operation-count, and budget limits.
- Precondition hash before modification.
- Atomic write or snapshot before mutation.
- Postcondition hash/content verification.
- Rollback record and quarantine on rollback failure.
- Kill check before and during every operation.

**Exit gate:** traversal, symlink escape, out-of-scope target, expired grant, wrong extension, oversized payload, and stale precondition all fail closed. Forced verification failure proves rollback and quarantine.

### Phase H — Sensors, witness correlation, and physical grounding

**Goal:** connect the node to the physical world without treating sensors as unquestionable truth.

**Prerequisite:** confirm the exact TC001 and ET140 hardware IDs, transport protocols, OS permissions, calibration data, sampling rates, and safe operating envelopes. No adapter is marked production-ready from a product name alone.

**Build:**

- Common `SensorAdapter` interface with observation, timestamp, device identity, calibration metadata, quality, and error state.
- TC001 read-only adapter.
- ET140 read-only adapter.
- Evidence witness for raw frames/measurements and normalized observations.
- Quorum/correlation engine that emits `AGREEMENT`, `PARTIAL`, `CONFLICT`, or `INSUFFICIENT`.
- Falsification Mirror that preserves conflicting evidence and freezes affected actions.
- No automatic memory deletion or “truth” rewrite on disagreement.

**Tests:** sensor agreement, disagreement, unavailable device, stale calibration, timestamp skew, corrupted frame, duplicate reading, unplug/replug, resource exhaustion.

**Exit gate:** sensor conflict creates uncertainty, preserves both evidence streams, blocks affected high-risk actions, and requests review or additional evidence.

### Phase I — Claims and semantic firewall

**Goal:** prevent unsupported model statements from becoming external facts or actions.

**Build:**

- Claim states: `CAPTURED → INFERRED → SUPPORTED → VERIFIED → TRANSMITTED`; alternate `CONTESTED → HUMAN_REVIEW`.
- Provenance requirements by claim type.
- Evidence freshness and confidence metadata.
- Transmission policy: internal captured claims, qualified inferred claims, evidence-linked supported claims, normal verified claims, blocked contested claims.
- External message/call capabilities remain disabled until the claim gate and approval gate both pass.
- Falsification Mirror integration freezes the action rather than erasing history.

**Tests:** unsupported claim, missing evidence, stale evidence, conflicting evidence, verified claim, operator override, transmission replay, policy version change.

**Exit gate:** no high-impact external transmission can occur with an unverified or contested claim.

### Phase J — Human acknowledgement and P0 escalation

**Goal:** make high-risk human control explicit and cryptographically traceable.

**Build:**

- Approval states: `ACTION_REQUIRES_ACK → ACK_REQUESTED → ACKNOWLEDGED|TIMEOUT → AUTHORIZED|ESCALATED`.
- Signed ACK bound to bind, action, target, risk, evidence, policy version, operator device, timestamp, and expiry.
- Human-readable approval detail, not raw model output.
- P0 state machine: `NORMAL → P0_DETECTED → AUTOMATION_HALTED → HUMAN_ACK_REQUESTED → HUMAN_CONTROL|ESCALATED`.
- Kill and quarantine remain dominant over ACK.
- Secondary ACK and timeout escalation are explicit, not silent retries.

**Tests:** forged ACK, stale ACK, wrong-target ACK, duplicate ACK, timeout, P0 halt, secondary escalation, resume/revoke, kill during approval.

**Exit gate:** a human can authorize exactly one described action, and no ACK can be reused to authorize another action.

### Phase K — iPhone application and operator console

**Goal:** expose the safe control loop without turning the phone into a shell.

**Build:**

- Convert the current Swift package into a real iOS SwiftUI target.
- Secure key storage, enrollment, tunnel status, session status, and device revocation.
- Home/status screen: node health, active tasks, approvals, quarantine, kill state.
- Text intent screen and task progress/history.
- Approval screen showing action, reason, target, risk, evidence, scope, rollback, expiration.
- Audit receipt and evidence viewer.
- Kill switch, acknowledge, reject, resume-review, and revoke controls.
- Voice only after text behavior is stable; voice is merely another input modality.

**Exit gate:** an owner submits a text request, sees the bind and result, approves a precise action, views evidence, and can kill the node. Offline/failure states do not queue unsafe mutations.

### Phase L — Approved shell, browser, GUI, and application agency

**Prerequisite:** Phases A–K green, plus security review.

**Build one capability at a time:**

1. `SHELL_EXEC`: isolated helper, strict executable/argument allowlist, scrubbed environment, timeout/resource limits, approval-only.
2. `APP_LAUNCH`: application allowlist and expected process/window verification.
3. `BROWSER_NAVIGATE`: destination allowlist, private browser profile, navigation evidence, no arbitrary downloads.
4. `SCREEN_READ`: screen capture and typed observation schema.
5. `GUI_CLICK`: expected application/window/state, coordinate confidence, after-state verification.
6. `GUI_TYPE`: focus verification, sensitive-field restrictions, after-state verification.
7. Composite workflows only after individual capabilities pass.

**Rules:** never trust exit code or click completion alone; observe and verify. Unexpected state stops execution and may quarantine after repetition. No blind retries.

**Exit gate:** wrong window, stale coordinates, unexpected dialog, closed app, changed screen, blocked download, and actuator mismatch all stop safely.

### Phase M — Hardening, integrity, and production operations

**Goal:** make the node operable and auditable under failure.

**Build:**

- Signed Vesta trust manifest containing runtime/policy/sensor registry hashes.
- Startup admission: verify manifest and fail closed on mismatch.
- Process separation: gateway, runtime, planner/model, executor, sensor/vision, and browser helpers.
- Least-privilege OS accounts and sandbox profiles.
- Network egress allowlist and secret isolation.
- Resource budgets: CPU, memory, runtime, tokens, network, file operations.
- Metrics and dashboard panels for decisions, ACKs, denials, evidence, conflicts, failures, recovery, quarantine, and kill.
- SQLite backup/restore and audit/evidence export.
- LaunchAgent supervision and documented local recovery runbook.
- Clean-machine installation and portability proof.

**Exit gate:** integrity mismatch blocks privileged startup; process compromise does not automatically grant host authority; backup restore preserves policy/audit/evidence relationships.

## 5. Verification matrix

Every phase runs these gates before the next phase starts:

| Gate | Requirement |
|---|---|
| Unit | All new contracts and deterministic rules tested |
| Integration | Boundary tested with real MSB components or a contract fixture |
| Adversarial | Bypass, replay, scope, injection, and tamper attempts fail |
| Recovery | Crash, timeout, restart, partial mutation, and rollback tested |
| Static | Pytest, mypy, Ruff, diff validation |
| Portability | Full suite from a foreign checkout path |
| Hygiene | Engineering battery and Qdrant safety sweep |
| Live | Health, route, policy, ledger, and safe smoke checks |
| Review | Threat model and capability diff reviewed before authority expands |

A phase is blocked if any gate fails. Green unit tests alone never authorize the next capability.

## 6. Final definition of done

### Identity and transport

- [ ] Mac and iPhone identities use hardware-backed keys where supported.
- [ ] Enrollment is explicit, revocable, and restart-persistent.
- [ ] Private WireGuard transport is required; raw executor is not public.
- [ ] Replay, expiry, revocation, and signature attacks fail.

### Governance and execution

- [ ] Every controlled action has an A-BIND, capability, scope, contract, budget, timeout, and policy version.
- [ ] The model cannot grant itself authority.
- [ ] High-risk actions require exact signed human ACK.
- [ ] Kill and quarantine dominate all execution paths.

### Evidence, verification, and recovery

- [ ] Every consequential action has before/after evidence and provenance.
- [ ] Completion requires postcondition verification.
- [ ] Recoverable mutations roll back; irreversible actions are explicit and approved.
- [ ] Audit-chain and evidence tampering fail closed and are detectable.

### Intelligence and memory

- [ ] MSB remains the sole reasoning/orchestration runtime.
- [ ] Model and retrieval providers are replaceable.
- [ ] Memory never equals authorization.
- [ ] Claims have evidence/freshness state before transmission.

### Physical agency

- [ ] Sensors preserve raw evidence and represent disagreement as uncertainty.
- [ ] Shell/browser/GUI capabilities are individually scoped and verified.
- [ ] Unexpected physical/UI state stops execution.
- [ ] Every capability has adversarial and recovery coverage.

## 7. Recommended next implementation gate

Do not jump to sensors or GUI. The next build is **Phase B–C hardening**:

1. replace the prototype crypto verification path with an approved provider/native key boundary;
2. complete Secure Enclave/Keychain persistence in the iPhone client;
3. deploy and interface-bind WireGuard;
4. prove public-interface denial and signed-session admission;
5. then extend the current Vesta A-BIND into the persisted task lifecycle.

That sequence increases trust without expanding actuator authority, and it preserves the existing MSB runtime while the perimeter becomes real.

## 8. Hardening increment completed

The first part of the next gate is now implemented:

- P-256 signing and verification use the declared `cryptography` provider while preserving the raw CryptoKit-compatible wire format.
- The Swift client has persistent Keychain storage and an iOS Secure Enclave path; lower-assurance software and macOS Keychain modes are explicit in `hardware_assurance`.
- Vesta has optional direct-peer tunnel admission controlled by `MSB_VESTA_REQUIRE_TUNNEL` and `MSB_VESTA_ALLOWED_CIDRS`.
- A WireGuard deployment and verification runbook exists at `docs/operations/vesta-wireguard.md`; no network interface or firewall state was changed automatically.

The persisted task lifecycle is now also implemented:

- Vesta tasks persist in SQLite with serialized transition validation.
- Controlled chat requests record `RECEIVED → AUTHENTICATED → PLANNED → AUTHORIZED → EXECUTING → VERIFYING → COMPLETED`.
- Denied requests terminate as `DENIED` before MSB execution.
- In-flight `EXECUTING`, `VERIFYING`, and `RECOVERING` tasks can be quarantined after restart rather than silently resumed.
- `/vesta/tasks/{task_id}` exposes operator-authenticated state and transition history.

Evidence/provenance is now also implemented for the controlled chat slice:

- Requests, deterministic policy decisions, and MSB responses become content-addressed evidence objects.
- Evidence metadata is durable in SQLite and blobs are written atomically under the configured Vesta evidence root.
- Task metadata, audit events, and API receipts carry evidence references.
- Evidence lookup verifies the stored hash and reports corruption or missing content without silently repairing it.

The first mutation capability is now implemented behind an exact approval contract:

- `/vesta/execute` accepts only a bounded UTF-8 file-write request and creates a pending approval.
- The approval binds the task, target path, payload evidence hash, expected precondition hash, policy version, and expiration.
- `/vesta/approvals/{id}/approve` revalidates the approval, kill switch, scope, payload evidence, and precondition before an atomic write.
- Write receipts contain before/after hashes; failures transition through recovery and quarantine.
- Rejection, path escape, stale precondition, oversized content, kill-switch state, and evidence corruption do not mutate the filesystem.

Signed-session admission is now implemented at `/vesta/signed-chat`:

- It reuses the durable device enrollment, P-256 signature, session, timestamp, and replay verifier.
- The device ID becomes the A-BIND actor.
- The server fixes the allowed chat capabilities; a signed client cannot self-authorize filesystem or shell access.
- Replay and tampered-signature requests fail before MSB execution.
- The Swift client now exposes a signed `chat(_:)` protocol method.

A hardware-independent development path now surrounds the missing phone/tunnel deployment:

- `msb_v3.vesta.dev_harness.LoopbackDevice` exercises the real enrollment, challenge, session, canonical signing, and signed-chat endpoints over loopback.
- `scripts/vesta-loopback.py` and `make vesta-loopback` provide a bounded local probe without storing a private key or weakening production admission.
- The loopback fixture deliberately requests `filesystem.write`; Vesta must ignore that client request and retain its server-owned Phase 0–2 capability set.
- The SwiftUI surface now shows Vesta/Node status, transport posture, signed-session enrollment, signed chat receipts, task state, evidence IDs, and the read-only file probe.
- `apps/iphone` includes decoding coverage for the status and task receipts.

This path proves protocol and UI integration now. It does not claim Secure Enclave attestation, WireGuard connectivity, or remote-interface firewall verification. The next gate remains actual private-overlay deployment/admission verification, followed by shell capability design and evidence-backed external claims.

## Read-only filesystem gate completed

The first Vesta filesystem capability is now implemented without opening host authority:

- `/vesta/read` and `/vesta/signed-read` create an actor-bound A-BIND with the server-owned `filesystem.read` capability.
- Reads are rooted at `MSB_NODE_SANDBOX_ROOT`; canonical path resolution rejects traversal, symlink escapes, missing targets, non-regular files, and oversized content.
- Every read records request, policy, raw content, and verification receipt evidence, then links those objects to the durable task and shared audit chain.
- Completion requires a returned-content SHA-256 match; reader errors, evidence failures, and kill state quarantine the task before any result is treated as complete.
- The Swift operator surface now uses `/vesta/signed-read`, so the phone's read probe no longer bypasses the Vesta trust boundary through the legacy Node route.

The `SHELL_EXEC` gate is now implemented as an approval-only development slice:

- `/vesta/shell/execute` accepts a named command plus bounded arguments; it never accepts a shell string or invokes `shell=True`.
- The initial server-owned allowlist contains only `echo` and `pwd`, mapped to fixed absolute executables. Arbitrary paths, shell flags, command substitution, network tools, and executable expansion are denied.
- Exact commands are persisted in a separate approval table with a canonical SHA-256 contract hash, policy version, and expiration.
- Approval revalidates the contract, kill switch, allowlist, process-group timeout, sandbox cwd, scrubbed environment, output bound, return code, and optional expected stdout.
- Output and execution receipts become evidence; mismatch, timeout, output truncation, tampering, and kill state quarantine the task.

This does not authorize general shell access. The next expansion requires a security review of each additional named executable and its argument schema before it is added to the allowlist.

## Cryptographic owner ACK gate completed

The exact shell approval contract can now be approved by an enrolled signed device:

- `/vesta/shell/approvals/{id}/signed-approve` verifies the durable `node.v1` session and replay state.
- The signed intent is bound to the route approval ID, exact command SHA-256, and policy version.
- Vesta independently compares those values with the durable approval before invoking the existing executor.
- The device cannot alter the command, scope, expiration, policy, or server-owned capability set.
- Replayed, wrong-contract, expired, already-decided, revoked-session, and tampered requests fail closed.
- The maintenance bearer-token path remains available as a distinct local recovery/operator path.
- Swift now exposes `approveShell(_:commandSHA256:policyVersion:)` and the operator surface displays the exact ACK fields.

The exact owner-ACK boundary is now also applied to `FILE_WRITE`:

- `/vesta/approvals/{id}/signed-approve` verifies the enrolled device and replay state.
- The signed intent binds approval ID, target path, payload hash, precondition hash, and policy version.
- Vesta compares all fields with the durable write approval before atomic mutation.
- Expiry is persisted as `EXPIRED`; wrong-target, replay, payload-tamper, kill, rollback, and verification failures remain fail-closed.
- Swift exposes `approveFileWrite(_:targetPath:payloadSHA256:expectedSHA256:policyVersion:)`.

The next gate is private-overlay deployment/admission verification, followed by security review before expanding the named shell allowlist or adding browser/application agency.
