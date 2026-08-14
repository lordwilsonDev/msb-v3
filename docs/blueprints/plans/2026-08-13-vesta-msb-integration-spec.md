# VESTA × MSB v3 Phase 0–2 Integration Specification

**Version:** 1.0  
**Status:** Approved for implementation from the supplied VESTA-NODE × MSB v3 blueprint  
**Date:** 2026-08-13  
**Scope:** Read-only discovery, A-BIND propagation, and append-only audit events

## 1. Context

MSB v3 already owns chat, memory, model routing, orchestration, governance state, and a SQLite-backed tamper-evident `AuditChain`. Vesta must not duplicate those capabilities. It becomes the controlled trust/evidence boundary around MSB.

The first slice must be deliberately narrow: observe the live MSB process, authorize a low-risk chat request with an immutable execution binding, invoke the existing MSB `/chat` harness, and record enough hash-chained events to reconstruct the request. Sensors, physical agency, external transmission, privileged tools, and a second memory system are explicitly deferred.

## 2. Functional Requirements

- **FR-001:** Vesta MUST expose read-only status, manifest, capability, route, and ledger-verification views.
- **FR-002:** Every controlled chat request MUST receive an immutable A-BIND containing bind/task/session IDs, actor, capabilities, risk class, deadline, cancellation ID, and policy version.
- **FR-003:** Vesta MUST authorize capabilities deterministically; model text MUST NOT set risk, capabilities, or approval state.
- **FR-004:** Phase 0–2 chat MUST allow only `model.inference` and `memory.read`; unknown, mutation, network, sensor, external, and tool capabilities MUST be denied.
- **FR-005:** Vesta MUST call the existing MSB chat harness and MUST NOT create a second orchestration or memory engine.
- **FR-006:** Vesta MUST record request, authorization, MSB completion/failure, and response events through the existing hash-chained `AuditChain` using component `vesta`.
- **FR-007:** Ledger verification MUST report invalid state and MUST NOT silently repair or delete records.
- **FR-008:** The Vesta chat endpoint MUST be closed unless `MSB_OPERATOR_TOKEN` is configured and the request carries the existing operator bearer credential.
- **FR-009:** Native `/chat` behavior MUST remain unchanged and available for local/internal use.
- **FR-010:** A-BIND context MUST be attached to the MSB request context for downstream propagation without changing the model prompt or granting authority.

## 3. Non-Functional Requirements

- **NFR-001:** Policy decisions MUST be deterministic and unit-testable without an LLM or network.
- **NFR-002:** The implementation MUST reuse `AuditChain`; no parallel ledger database or hash algorithm may be introduced.
- **NFR-003:** Failure at policy, deadline, or ledger append MUST fail closed; a chat result MUST NOT be returned as Vesta-approved when its audit trail cannot be recorded.
- **NFR-004:** All new Python code MUST pass mypy and Ruff under the repository's configured gates.
- **NFR-005:** Existing tests and endpoint contracts MUST remain green.

## 4. Acceptance Criteria

- **AC-001 [FR-001]:** Given a running app, when `GET /vesta/status`, `/vesta/manifest`, `/vesta/capabilities`, `/vesta/routes`, and `/vesta/ledger/verify` are called, then they return structured read-only data.
- **AC-002 [FR-002, FR-010]:** Given an authorized chat request, when the adapter invokes MSB, then the response includes a bind ID and the MSB request context contains the same bind.
- **AC-003 [FR-003, FR-004]:** Given allowed, unknown, mutation, network, or sensor capabilities, when policy evaluates them, then only the two Phase 0–2 capabilities are allowed.
- **AC-004 [FR-005, FR-009]:** Given a test harness, when Vesta chat runs, then it uses the injected/native `ChatHarness` and native `/chat` remains callable.
- **AC-005 [FR-006]:** Given a successful Vesta chat, when the ledger is read, then request, authorization, MSB completion, and response events share the bind ID and verify as one valid chain.
- **AC-006 [FR-007]:** Given a tampered ledger record, when verification runs, then it reports `valid=false` and no repair occurs.
- **AC-007 [FR-008]:** Given no operator token, when controlled chat is called, then it returns 503; given a wrong token, it returns 401.
- **AC-008 [FR-003, FR-004]:** Given a request that attempts to self-authorize `filesystem.write`, `network.allowlist`, `sensor.read`, or `external.message`, then Vesta denies it before MSB is called.

## 5. Edge Cases

- **EC-001:** Empty or oversized chat query is rejected by request validation.
- **EC-002:** Deadline already expired before MSB invocation is denied.
- **EC-003:** MSB raises or returns an unsuccessful result; Vesta records failure and does not return approved completion.
- **EC-004:** Audit append fails; Vesta returns a fail-closed server error rather than an apparently successful result.
- **EC-005:** Ledger tampering is detected at any record, including the first or last record.
- **EC-006:** Duplicate request IDs do not grant additional authority; each A-BIND is independently identified and audit-linked.

## 6. API Contracts

```text
POST /vesta/chat
Authorization: Bearer <MSB_OPERATOR_TOKEN>
{
  "query": string,
  "session": string = "default",
  "system": string | null,
  "capabilities": string[] = ["model.inference", "memory.read"]
}
→ 200 {
  "ok": true,
  "bind_id": string,
  "decision": "ALLOW",
  "policy_version": "vesta-policy-1",
  "payload": {"query": string, "text": string, "model": string},
  "audit_event_ids": number[]
}

GET /vesta/status
GET /vesta/manifest
GET /vesta/capabilities
GET /vesta/routes
GET /vesta/ledger/verify
POST /vesta/authorize
```

Denied requests return 403 with a structured decision. Unconfigured or invalid operator authentication returns 503/401 using the existing auth contract. Ledger failure returns 503.

## 7. Data Models

| Entity | Required fields |
|---|---|
| A-BIND | bind_id, task_id, parent_task_id, session_id, actor, capabilities, risk_class, deadline, cancellation_id, policy_version, evidence_required |
| Policy decision | decision, risk_class, capabilities, reasons, policy_version |
| Vesta ledger event | bind_id, event type, actor, action, decision, policy version, evidence refs |
| Manifest | node/runtime identity, MSB version, policy version, capability profile, creation timestamp |

## 8. Out of Scope

- **OS-001:** TC001, ET140, sensor quorum, or physical-device adapters.
- **OS-002:** Filesystem write, shell, GUI, browser, MCP invocation, external messaging, and network capabilities.
- **OS-003:** Phone UI, voice, WireGuard deployment, Secure Enclave persistence, and enrollment UX.
- **OS-004:** New memory, provenance, or ledger databases; existing MSB memory and `AuditChain` remain authoritative.
- **OS-005:** Automatic ledger repair, claim verification, semantic firewall, P0 escalation, Docker sandboxing, and signed runtime manifests.
