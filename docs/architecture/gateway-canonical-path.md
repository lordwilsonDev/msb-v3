# Gateway Canonical Path

**Date:** 2026-08-28 · **Status:** Active architecture record
**Convergence blocker:** C2 (Final Convergence Blueprint §9–§12)

## Purpose

This document answers: **where does capability resolution happen in MSB v3,
and should every governed execution path go through the gateway?**

---

## Current State (Honest)

There are **three orthogonal governance layers** that control what the system
is allowed to do:

| Layer | Module | Responsibility | Fail-mode |
|---|---|---|---|
| **Gateway** | `gateway/route.py` | Compute-plane routing: capability check, authorization check, backend selection (local vs. frontier). Audit-logged to `msb_ledger`. | Fail-closed: deny on error |
| **ActionGate** | `agent/safety.py` | Tool execution gating: risk tier + taint check. Blocks tainted writes, escalates to REVIEW. Audit-logged to `msb_ledger`. | Fail-closed: deny on error |
| **KillSwitch** | `governance/killswitch.py` | Emergency shutdown: global or scoped (tenant/agent/task/tool/capability/resource). SQLite-persisted. | Fail-closed: ARMED on unreadable state |

### Execution Paths

**Path 1: `/chat` (ChatHarness)**
```
POST /chat
  → check_auth
  → ChatHarness.execute()
    → gateway.route(GatewayCall(...))     ← GATEWAY INVOLVED
    → active_backend() / get_client()
    → client.chat()
```

Gateway IS the authority here. The chat path routes through gateway for
capability check + backend selection.

**Path 2: `/agent/handle` (Agent Slice)**
```
POST /handle
  → require_operator
  → agent.handle.handle()
    → _resolve_agent()                    (identity lookup)
    → _quick_reject_gate()                (MoIE pre-filter)
    → interpret_intent()                  (model call)
    → plan()                              (model call via resolve_client())
    → execute_graph()
      → SafeProvider → ActionGate.gate()  (tool gating)
      → ToolProvider.execute()            (tool execution)
    → verify()
    → record_trace()
```

Gateway is NOT involved. The agent path uses:
- `fabric.model_router.resolve_client()` for model selection
- `agent.safety.ActionGate` for tool gating
- `governance.killswitch.KillSwitch` for emergency shutdown

**Path 3: `/v1` (OpenAI Compat)**
```
POST /v1/chat/completions
  → Routes to ChatHarness (gateway involved)
  OR direct model call (gateway NOT involved)
```

### Where Gateway IS Called

Only one production caller: `harnesses/base.py` (ChatHarness).
SURFACE.md classifies both `msb_v3/gateway` and `msb_v3/harnesses` as OPTIONAL.

### Where Gateway is NOT Called

- `agent/handle.py` — the canonical governed loop
- `api/agent.py` — the HTTP entry point for the governed loop
- `fabric/model_router.py` — the model selection layer
- `agent/safety.py` — the tool execution gate

---

## The Architectural Question

The Final Convergence Blueprint (§9) states:

> **Every governed capability resolution path goes through the canonical gateway.**

Currently this is false. The agent path — the most governed, most audited,
most consequential execution path in the system — bypasses the gateway entirely.

### Why This Happened

The gateway was designed as a **compute-plane router** (local vs. frontier
based on memory budget). The agent path was designed as a **governed execution
loop** (intent → plan → gated tools → verify → evidence). They solve
different problems:

- Gateway asks: "Given these capability tokens, is this call authorized and
  where should the compute happen?"
- ActionGate asks: "Given this tool call's risk tier and taint status, should
  it execute?"
- ModelRouter asks: "Given this task kind and privacy scope, which LLM
  client handles it?"

These are orthogonal concerns. Making gateway "canonical" for the agent path
means one of two things:

**Option A: Gateway absorbs ActionGate.** The gateway becomes the single
authority for "is this allowed?" — including tool-level risk tier and taint
checks. This is a significant refactor that would unify the governance model
but requires rewriting the agent path's safety layer.

**Option B: Gateway becomes the audit entry point.** The agent path calls
`gateway.route()` to record the compute decision (what capabilities were
requested, which backend was selected) into the audit chain, but the actual
authorization still lives in ActionGate. The gateway becomes a
**recording layer** that ensures every governed execution leaves an audit
trail, while ActionGate remains the enforcement layer.

**Option C: Document the dual-governance model honestly.** The gateway and
ActionGate are both governance layers with different responsibilities.
The bypass test verifies that no governed execution path runs without
at least one governance layer, and the architecture is documented as
having two orthogonal enforcement points rather than a single canonical path.

---

## Target State (This Decision)

**Chosen: Option B — Gateway as audit entry point + ActionGate as enforcement.**

Rationale:
1. The gateway's capability-check + backend-selection model is the right
   abstraction for compute routing. ActionGate's risk-tier + taint model is
   the right abstraction for tool gating. Unifying them would conflate two
   different concerns.
2. The gateway already writes to the audit chain. Making the agent path call
   `gateway.route()` before executing ensures the compute decision is recorded
   even when ActionGate handles the enforcement.
3. This is the minimum change that satisfies the convergence blueprint's intent
   ("every governed execution path goes through the gateway") without rewriting
   the agent path's governance model.

### The Invariant

```
GOVERNED EXECUTION
       ↓
    GATEWAY          (audit: record the compute decision)
       ↓
  ActionGate         (enforce: risk tier + taint check)
       ↓
    execution
```

Every governed execution path must:
1. Call `gateway.route()` to record the compute decision (audit trail)
2. Call `ActionGate.gate()` to enforce tool-level authorization
3. Record evidence on the Evidence Spine

A path that skips step 1 is a **gateway bypass** (the bypass test catches this).
A path that skips step 2 is an **ActionGate bypass** (the safety tests catch this).
A path that skips step 3 is an **evidence bypass** (the spine tests catch this).

---

## Implementation

### What Changed

`agent/handle.py` now calls `gateway.route()` at the top of `handle()`,
before any model calls. This records the compute decision (task kind,
estimated bytes, capabilities) into the audit chain. The gateway's
authorization result is logged but does NOT block execution — ActionGate
remains the enforcement layer for tool-level gating.

### What Did NOT Change

- ActionGate still enforces risk tier + taint checks on every tool call
- KillSwitch still provides emergency shutdown
- ModelRouter still selects the LLM client
- The gateway's backend selection (local vs. frontier) is informational
  for the agent path — the agent path always uses the local client

### The Bypass Test

`tests/architecture/test_gateway_canonical.py` verifies:
1. `agent.handle.handle` imports and calls `gateway.route`
2. `harnesses.base.ChatHarness.execute` imports and calls `gateway.route`
3. No other production path executes governed tools without gateway or
   ActionGate in its call chain

---

## Relationship to Other Governance

| Concern | Authority | Module |
|---|---|---|
| Compute routing (local vs. frontier) | Gateway | `gateway/route.py` |
| Tool execution authorization | ActionGate | `agent/safety.py` |
| Emergency shutdown | KillSwitch | `governance/killswitch.py` |
| Operator approval | ApprovalQueue | `governance/approval.py` |
| Budget enforcement | BudgetLedger | `governance/budget.py` |
| Convergence enforcement | OuroborosGovernor | `governance/governor.py` |
| Evidence provenance | Evidence Spine | `evidence/spine.py` |
| Audit trail | AuditChain | `msb_ledger/audit_chain.py` |

These are orthogonal, not redundant. Each answers a different question.
The gateway is the **recording entry point**; ActionGate is the
**enforcement layer**; the audit chain is the **provenance record**.
