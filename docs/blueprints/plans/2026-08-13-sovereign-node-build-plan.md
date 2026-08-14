# Sovereign Node — Build Plan

**Version:** 1.0  
**Date:** 2026-08-13  
**Status:** Implementation in progress — first vertical slice selected  
**Scope:** Mac Mini node, authenticated iPhone client, policy-governed MSB v3 adapter  
**Primary principle:** `Identity → Intent → Policy → Intelligence → Execution → Verification → Audit → Recovery`

## 1. Decision summary

Sovereign Node will be built as a bounded integration layer around the existing
`msb_v3` runtime, not as a second runtime and not as a remote shell.

Initial repository shape:

```text
msb-v3/
├── src/msb_v3/node/          # Mac-side node boundary and orchestration adapter
├── apps/iphone/               # Separate SwiftUI client target
├── docs/blueprints/           # Architecture and protocol decisions
└── tests/node/                # Node-specific unit, integration, adversarial,
                              # and recovery tests
```

The Mac gateway remains inside the existing FastAPI process initially, mounted
under `/node/v1`. A later deployment phase may expose `/v1` through a private
reverse-proxy alias, but the executor will never be directly public.

Existing components to reuse rather than rebuild:

| Sovereign Node concern | Existing MSB v3 foundation | Planned adapter work |
|---|---|---|
| Governance brakes | `governance/guard.py`, budget, approvals, kill switch | Add node action context and capability-aware policy inputs |
| Audit | `uac/audit_chain.py` | Add signed request/action/verification/recovery event schemas |
| Planning | `agent/intent.py`, `agent/planner.py`, `agent/dag.py` | Require policy-owned capabilities and contracts on every task |
| Execution | `agent/executor.py`, bridge providers | Add explicit OS capability providers behind a node boundary |
| Verification | `agent/verify.py` | Add capability-specific postconditions and evidence receipts |
| Memory/RAG | `retrieval/`, `memory/`, Qdrant, SQLite | Route context by memory domain; never treat memory as authorization |
| Model routing | `fabric/`, Ollama and llama.cpp interfaces | Keep model choice replaceable and non-authoritative |
| Service/API | FastAPI app factory and existing auth module | Add signed device sessions; do not use the operator bearer token as phone identity |

## 2. Non-negotiable laws

1. **The model never has authority.** Model output is an untrusted proposal.
2. **Capabilities are explicit.** There is no unrestricted “control the Mac” permission.
3. **Policy assigns risk.** The intent compiler may request capabilities, but it may not self-assign approval or risk.
4. **Mutations produce evidence.** Intent, actor, policy, capability, target, arguments, result, verification, and recovery metadata are auditable.
5. **Verification is required for completion.** Exit code zero is not success by itself.
6. **Kill and quarantine dominate all other decisions.** An armed kill switch or quarantined node denies mutations.
7. **Private transport is mandatory.** No executor endpoint is exposed directly to the public internet.
8. **Memory is not authorization.** Familiarity, historical behavior, or retrieved context never substitutes for policy or approval.
9. **No GUI-first build.** The first actuator is read-only and deterministic.
10. **No speculative subsystems.** The deferred `event_bus`, entity graph, Love/alignment layer, and fuzzy skill matching are not prerequisites for the first vertical slice.

## 3. Trust boundaries and threat model

### Trusted or policy-controlled

- Mac node identity private key, hardware-protected where supported
- iPhone device key, hardware-protected where supported
- Explicitly configured policy and capability registry
- Approval decisions made through the authenticated owner session
- Audit verification and operator-controlled recovery

### Untrusted

- Natural-language input
- LLM output and model-selected tools
- Retrieved documents and memory
- iPhone network location and transport metadata
- Capability arguments, file paths, URLs, shell text, and GUI coordinates
- External network responses
- Local actuator results until verified

### Threats that must be regression-tested

- Replayed, expired, duplicated, or reordered requests
- Invalid signatures and unknown/revoked device keys
- Path traversal and scope expansion
- Capability substitution (`FILE_READ` becoming `FILE_WRITE`)
- Model-proposed risk or approval escalation
- Approval reuse across requests or targets
- Kill-switch bypass and quarantine bypass
- Stale capability tokens
- Shell injection and shell allowlist escape
- Verification spoofing or checking the wrong target
- Audit-chain tampering or audit-write failure
- Partial mutation, crash, timeout, and restart between execution stages
- GUI actions against the wrong application/window/state

## 4. Canonical data contracts

### 4.1 Node identity

```text
NodeIdentity
  node_id: str
  public_key: str
  certificate_or_attestation: optional str
  created_at: datetime
  key_version: int
  device_binding: str
  status: ACTIVE | LOCKED | QUARANTINED | REVOKED
```

The private key MUST NOT be exportable. The first implementation SHOULD use
Apple Secure Enclave-backed signing keys through the platform APIs where the
hardware supports it. If attestation is unavailable, enrollment MUST mark the
identity as lower assurance rather than pretending hardware proof exists.

### 4.2 Device and session

```text
RegisteredDevice
  device_id: str
  public_key: str
  display_name: str
  enrolled_at: datetime
  last_seen_at: datetime
  status: ACTIVE | LOCKED | REVOKED

AuthenticatedSession
  session_id: str
  device_id: str
  issued_at: datetime
  expires_at: datetime
  last_nonce: str
  status: ACTIVE | EXPIRED | REVOKED
```

### 4.3 Signed request envelope

```text
SignedRequest
  request_id: UUID
  session_id: str
  timestamp: datetime
  nonce: str
  intent: StructuredIntent
  signature: str
  key_version: int
```

The signature covers a canonical, versioned serialization of every field
except the signature itself. The server rejects unknown versions, clock-skewed
requests, nonce reuse, duplicate request IDs, invalid signatures, inactive
sessions, and revoked devices.

### 4.4 Structured intent

```text
StructuredIntent
  intent_id: UUID
  type: str
  objective: str
  target: dict
  requested_capabilities: list[Capability]
  user_constraints: list[str]
  source: text | voice | system
  compiler_version: str
```

The compiler MUST NOT accept model-supplied `risk`, `requires_approval`, or
`decision` fields as authoritative. The policy engine derives those values.

### 4.5 Policy decision

```text
PolicyDecision
  decision: ALLOW | ALLOW_WITH_LIMITS | REQUIRE_APPROVAL | DENY | QUARANTINE
  risk_level: L0..L7
  reasons: list[str]
  capabilities: list[CapabilityGrant]
  approval_id: optional str
  contract_id: optional str
  expires_at: datetime
  policy_version: str
```

### 4.6 Capability grant

```text
CapabilityGrant
  grant_id: UUID
  capability: SCREEN_READ | FILE_READ | FILE_WRITE | SHELL_EXEC |
              GUI_CLICK | GUI_TYPE | BROWSER_NAVIGATE | NETWORK_REQUEST |
              APP_LAUNCH
  scope: dict
  issued_for_request: UUID
  approval_id: optional str
  max_operations: int
  expires_at: datetime
  status: ACTIVE | USED_UP | EXPIRED | REVOKED
```

The executor MUST revalidate the grant immediately before every operation.

### 4.7 Action contract and receipt

```text
ActionContract
  contract_id: UUID
  action: str
  capability: Capability
  target_scope: dict
  argument_constraints: dict
  budget: dict
  timeout_s: float
  verification_method: str
  rollback_strategy: str
  approval_required: bool

ExecutionReceipt
  execution_id: UUID
  request_id: UUID
  task_id: str
  policy_decision: str
  capability_grant_id: UUID
  started_at: datetime
  finished_at: datetime
  result: dict
  verification: dict
  rollback: dict
  audit_event_ids: list[int]
```

## 5. Build phases and gates

### Phase 0 — Architecture freeze and protocol specification

**Work**

- Add ADRs for trust boundaries, signed-envelope canonicalization, capability taxonomy, risk matrix, state machine, and audit schema.
- Define clock-skew tolerance, nonce retention, session lifetime, request-size limits, and failure defaults.
- Define which existing MSB governance functions are reused directly and where node-specific policy wraps them.
- Define the enrollment ceremony and recovery procedure before writing device-auth code.
- Create a threat-to-test matrix from §3.

**Exit gate**

- Protocol fixtures exist for valid, invalid, expired, replayed, and revoked requests.
- Every capability has a scope model, risk level, approval rule, and verification strategy.
- No open decision remains about what the policy engine—not the model—owns.

### Phase 1 — Mac node identity and enrollment

**Work**

- Implement `msb_v3/node/identity/` and SQLite identity tables.
- Generate or load a non-exportable Mac signing key using Secure Enclave-backed APIs where available.
- Store only public identity metadata in the application database.
- Implement device enrollment using a one-time, operator-approved ceremony; do not create an open registration endpoint.
- Implement device status transitions: active, locked, quarantined, revoked.
- Add key versioning and explicit rotation/revocation records.

**Exit gate**

- Mac signs a challenge and the verifier confirms the registered public key.
- Private key export is impossible through the application API.
- Revoked or quarantined identities cannot start mutation executions.
- Restart preserves identity and status.

### Phase 2 — Authenticated session and private transport

**Work**

- Implement `/node/v1/auth/challenge` and `/node/v1/auth/session`.
- Implement signed request verification middleware for `/node/v1/engage` and control endpoints.
- Add replay protection using request ID + session nonce persistence, not an in-memory set.
- Enforce timestamp skew, session expiration, device revocation, and signature versioning.
- Run only over a private authenticated overlay. Select the overlay provider in an operations ADR; do not expose the node directly to the public internet.
- Keep existing `MSB_OPERATOR_TOKEN` for local/operator maintenance, separate from phone device identity.

**Exit gate**

- A registered iPhone test client can establish a session and submit a signed request.
- Invalid signature, reused nonce, duplicate request, expired request, expired session, unknown session, and revoked device each fail deterministically.
- Transport-level encryption and application-level request authentication are both present.
- No executor route is reachable without the node gateway and signed session.

### Phase 3 — Intent compiler and policy engine

**Work**

- Implement `msb_v3/node/intent/` for text-first structured intent compilation.
- Treat model output as untrusted JSON validated against the intent schema.
- Implement `msb_v3/node/policy/` with explicit capability registry, risk matrix L0–L7, target scope checks, approval rules, and deny/quarantine decisions.
- Reuse the existing approval queue, kill switch, budget ledger, and audit chain through a node policy adapter.
- Add idempotent approval binding: an approval is bound to one request, contract, target scope, and capability set.
- Ensure policies are versioned and included in every decision receipt.

**Initial policy surface**

| Capability | First status | Default risk |
|---|---|---:|
| `SCREEN_READ` | deferred until physical-agent phase | L1 |
| `FILE_READ` | first actuator | L1 |
| `FILE_WRITE` | after read-only slice | L2/L3 |
| `SHELL_EXEC` | approval-only, allowlist only | L4 |
| `GUI_CLICK`, `GUI_TYPE` | deferred | L4/L5 |
| `BROWSER_NAVIGATE` | deferred, constrained | L3/L4 |
| `NETWORK_REQUEST` | explicit destination policy | L3/L4 |
| `APP_LAUNCH` | deferred | L2/L3 |

**Exit gate**

- The same intent receives the same decision under the same policy version.
- Model-proposed risk/approval fields cannot elevate authority.
- High-risk actions produce a durable pending approval and do not execute.
- Kill switch and quarantine return deny before planning or actuator invocation.

### Phase 4 — Runtime adapter and task lifecycle

**Work**

- Implement `msb_v3/node/runtime/` as the adapter between signed requests and existing `agent` DAG execution.
- Add a node task state machine:
  `RECEIVED → AUTHENTICATED → PLANNED → AUTHORIZED → APPROVED → EXECUTING → VERIFYING → COMPLETED`.
- Add failure transitions to `RECOVERING` and `QUARANTINED`.
- Extend task metadata with request ID, policy decision, capability grants, contract, budgets, timeout, rollback, and verification method.
- Call policy and capability validation before every task, not only at plan creation.
- Persist task and execution state in SQLite so restart recovery cannot silently re-run or skip a mutation.
- Connect existing planner/executor/verifier only through this adapter; no direct model-to-tool path.

**Exit gate**

- A read-only request completes end-to-end through the state machine.
- Restart at every state boundary produces a deterministic recovery decision.
- A failed or skipped parent prevents dependent tasks from running.
- Every state transition has an audit event and request correlation ID.

### Phase 5 — Safe filesystem actuator

**Work**

Build only these capabilities first:

1. `FILE_READ` within an allowlisted root.
2. `FILE_WRITE` for new files within an allowlisted root.
3. Controlled modification only after snapshot/rollback support is proven.

Implement `msb_v3/node/capabilities/filesystem/` with:

- canonical-path resolution and traversal rejection;
- symlink and mount-boundary policy;
- extension, size, operation-count, and root-scope limits;
- precondition capture;
- atomic write or versioned snapshot;
- deterministic postcondition verification;
- rollback receipt;
- no implicit shell fallback.

**Exit gate**

- Read outside scope is denied.
- Write outside scope, path traversal, symlink escape, extension violation, size violation, and expired grant are denied.
- A new file can be created and verified.
- A controlled modification can be rolled back after forced verification failure.
- Kill during execution prevents subsequent mutations.

### Phase 6 — Audit, verification, recovery, and quarantine hardening

**Work**

- Define node audit event types: request received, authentication, intent compiled, policy decision, approval, capability issued, contract accepted, execution, observation, verification, rollback, quarantine, kill, resume, and recovery.
- Reuse `AuditChain`, but distinguish tamper-evident local persistence from external immutability. Add signed/exportable audit snapshots before claiming immutable archival.
- Add deterministic verification plugins for filesystem existence, size, hash, content, and structured output.
- Implement recovery records with before/after evidence and rollback status.
- Implement automatic quarantine triggers for signature failure bursts, policy bypass attempts, unexpected mutation, repeated verification failure, capability mismatch, audit inconsistency, and resource anomalies.
- Require explicit operator review to resume a quarantined node.

**Exit gate**

- Audit verification detects tampering and identifies the first break.
- An audit-write failure is surfaced and blocks or quarantines mutation according to policy; it is never silently ignored.
- Recovery is idempotent and restart-safe.
- Quarantine survives restart and denies all mutations until explicit review.

### Phase 7 — iPhone text client and approvals

**Work**

- Create a native SwiftUI app under `apps/iphone/` using platform cryptography and secure key storage.
- Implement enrollment, session establishment, text intent submission, task status, approval detail, approve/reject, audit receipt, kill, and resume-review screens.
- Make approval screens human-readable: action, target, reason, risk, scope, affected resources, rollback, and expiration.
- Do not add voice yet; text provides the smallest authenticated vertical slice.
- Add network failure and offline UI states without queuing unsafe mutations locally.

**Exit gate**

- An owner can submit a read-only request from iPhone and view its result.
- An owner can approve a high-risk action and see the exact bound contract.
- The app cannot approve a different target or reuse an old approval.
- Kill from the iPhone blocks a pending or active mutation.

### Phase 8 — Model routing and memory domains

**Work**

- Place intent extraction, planning, retrieval, and vision behind replaceable interfaces.
- Keep Ollama as the verified active backend; do not claim llama.cpp fallback readiness until model weights and a clean-box test exist.
- Map existing retrieval into episodic, semantic, procedural, and constitutional domains where the data contract supports it.
- Keep constitutional policy local and deterministic; it must not be retrieved as ordinary context.
- Add provenance references to retrieved context, but never use memory as authorization.
- Add model/embedding budget accounting through existing governance budgets.

**Exit gate**

- Swapping the model provider does not change policy decisions or capability checks.
- Retrieval failure degrades to a safe no-action result rather than invented context.
- Constitutional policy cannot be overridden by retrieved text or model output.
- Each memory-backed decision contains source references and freshness metadata.

### Phase 9 — Approved shell, browser, GUI, and voice capabilities

This phase starts only after Phases 1–8 are green.

#### Shell

- Separate helper process with an allowlist, environment scrub, timeout, resource budget, and no arbitrary command interpolation.
- Every command has explicit arguments, scope, output limits, and verification.
- L4+ approval is mandatory; destructive or irreversible actions are owner-only.

#### Browser and application control

- Add `BROWSER_NAVIGATE` and `APP_LAUNCH` with destination/application allowlists.
- Keep browser automation behind a separate actuator boundary.
- Capture before/after evidence and stop on unexpected state.

#### GUI and physical agency

- Add screen capture, observation schema, confidence threshold, expected application/window checks, and post-action verification.
- Implement one capability at a time: screen read, click, type, then browser workflows.
- Never retry blindly after a mismatched screen state; stop and escalate.

#### Voice

- Add speech-to-text only after the text protocol is stable.
- Voice output is treated exactly like typed intent and receives no additional authority.

**Exit gate**

- Each new actuator has unit, integration, adversarial, and recovery tests before exposure.
- Wrong window, stale coordinates, unexpected dialog, closed app, and screen-change cases stop execution.
- No physical action can bypass policy, contract, capability revalidation, verification, audit, or kill switch.

## 6. Test strategy

### Unit

- Canonical signing bytes and signature verification
- Identity status transitions and revocation
- Session expiry, nonce/request replay, and idempotency
- Intent schema validation
- Capability scope and risk matrix
- Policy determinism and approval binding
- Contract validation and budget enforcement
- Filesystem path safety and rollback
- Verification receipts and failure classification
- Audit-chain integrity and event schema
- Quarantine and kill-switch precedence

### Integration

- iPhone test client → gateway → signed session
- Gateway → intent compiler → policy
- Policy → existing MSB planner/DAG/executor adapter
- Capability provider → observation → verification → audit
- Approval round-trip and restart persistence
- Crash/restart at every task state

### Adversarial

- Signature forgery, stale timestamps, nonce reuse, request duplication
- Path traversal, symlink escape, scope widening, extension bypass
- Model claims `ALLOW`, requests undeclared capabilities, or changes target after approval
- Approval replay against another request
- Kill-switch and quarantine race conditions
- Audit tamper, audit storage failure, and chain repair
- Shell injection and environment leakage
- Malformed DAG, cycle, timeout, retry storm, and resource exhaustion

### Recovery

- Process crash before and after mutation
- Power/network loss during approval and execution
- Verification failure after partial write
- Rollback failure requiring quarantine
- Model timeout and provider substitution
- Revocation during an active task

### Definition of done per capability

A capability is not shippable until it has:

```text
policy rule
scope model
contract
budget
timeout
precondition
executor
postcondition verification
rollback or explicit irreversible approval
audit events
kill/quarantine behavior
unit + integration + adversarial + recovery tests
```

## 7. Operational requirements

- Private overlay only; no direct public executor exposure.
- Mac node supervised by the existing LaunchAgent process model.
- Node databases use dedicated SQLite paths and backup/restore procedures.
- Secrets and private keys never enter logs, prompts, audit payloads, or iPhone analytics.
- Metrics cover policy decisions, approvals, execution outcomes, verification failures, recovery, quarantine, and kill events.
- `/node/v1/status` is read-only and safe to expose through the authenticated gateway.
- The node must have a documented local recovery path when the iPhone, overlay, model, or database is unavailable.

## 8. Explicitly out of scope for the first vertical slice

- Public internet exposure
- Arbitrary shell execution
- GUI automation, browser control, and physical agency
- Voice input
- Automatic device enrollment
- Self-modifying policy
- Model-controlled authorization or risk assignment
- Durable entity/relationship graph construction
- Event-bus adoption into the live runtime
- Love/alignment score as a security gate
- Docker packaging as a prerequisite
- Claiming llama.cpp fallback readiness without model weights and clean-box evidence

## 9. First implementation slice

The first build should be deliberately narrow:

```text
iPhone text request
  → signed session
  → structured read-only intent
  → deterministic policy decision
  → FILE_READ capability grant
  → existing MSB task DAG adapter
  → allowlisted filesystem read
  → grounded verification
  → hash-chain audit receipt
  → iPhone result
```

It must also prove the negative path:

```text
same flow with FILE_WRITE or SHELL_EXEC
  → REQUIRE_APPROVAL or DENY
  → no actuator call
  → durable policy/audit evidence
```

This slice is the recommended starting point because it proves the core
inversion—authenticated human intent becomes a constrained, verified action—
without introducing GUI fragility or a second orchestration runtime.

## 10. Chosen decisions

1. **Transport:** self-hosted WireGuard private overlay. The node will bind its
   gateway to the tunnel interface and will not expose an executor port to the
   public internet. No managed auth/network vendor is a runtime dependency.
2. **Platform crypto:** P-256 signatures with Secure Enclave-backed keys on
   supported macOS/iOS targets; the protocol records `hardware_assurance` and
   falls back to Keychain-protected software keys only as explicitly lower
   assurance enrollment.
3. **Enrollment:** one-time local enrollment using a short-lived pairing code
   and QR/manual public-key exchange. Enrollment is disabled after pairing and
   requires the local operator console to re-enable it.
4. **Initial scope:** a configured development root under the MSB repository's
   `runtime/node-sandbox/` directory. `FILE_READ` is the first capability;
   arbitrary home-directory reads are denied.
5. **iPhone packaging:** a SwiftUI client target under `apps/iphone/` with a
   versioned protocol fixture. The Mac gateway remains the source of truth for
   policy and state.
6. **Approval ownership:** one enrolled owner device for v1. Additional devices
   are read-only until an explicit multi-device policy is designed.
7. **Audit immutability:** v1 claims tamper-evident local SQLite hash-chain
   persistence, not external WORM immutability. Signed audit exports are a
   later operations phase.

These choices are the implementation baseline. A future change requires an
ADR and must not silently broaden authority or weaken the gates.

## 11. Implementation record

The first vertical slice is implemented:

- `src/msb_v3/node/` contains the signed P-256 protocol, durable device
  enrollment, session/replay state, deterministic policy, node-scoped mutation
  approvals, scoped filesystem reader, audit integration, and FastAPI gateway.
- `apps/iphone/` contains a Swift Package using CryptoKit plus a minimal
  SwiftUI text client for enrollment, authentication, and read-only requests.
- The gateway is mounted at `/node/v1`; the active runtime remains supervised
  by the existing MSB LaunchAgent.
- The first slice allows only scoped `FILE_READ`. `FILE_WRITE` creates a durable
  pending node approval and never executes; shell, GUI, browser, network, and
  app-launch capabilities are denied.

Verified on 2026-08-13:

- Python: 813 passed, 3 intentional live-test skips
- Mypy: clean across 126 source files
- Ruff: clean
- Swift package: 1 test passed
- Portability: 813 passed, 3 skipped from a foreign checkout
- Hygiene: 12/12 experiments passed
- Live `/node/v1/status`: ACTIVE; MSB app/Ollama/DB healthy

Explicit remaining work before calling hardware-backed production readiness:

- Replace the Swift client's in-memory key default with Secure Enclave/Keychain
  persistence and an actual iOS app target.
- Complete WireGuard deployment and verify interface-bound exposure.
- Add the owner approval UI/route for node-scoped pending mutations.
- Independently review the dependency-free P-256 implementation or replace it
  with an approved audited crypto provider.
- Add controlled FILE_WRITE only after rollback and postcondition verification
  are implemented.
