# Canonical Governance Decision Object v1

**Status:** Phase 4 deliverable.  
**Source:** `src/msb_v3/governance/decision.py`.

---

## What this is

A single governance contract shared by:

- the capability resolver (proposes a capability + confidence + evidence)
- the action gate (decides ALLOW / REVIEW / BLOCK / UNKNOWN)
- the evidence receipt (records the decision chain)
- shadow mode (records old vs new decisions)

The object records *what the system decided*, not *what happened*. That
distinction matters: the receipt will later record the full decision chain, and
this object is one vertebra in that chain.

---

## Decision values

| value | meaning |
|-------|---------|
| ALLOW | the system may execute |
| REVIEW | the system must obtain approval before executing |
| BLOCK | the system must not execute |
| UNKNOWN | the system does not yet know what capability this is |

UNKNOWN is a **first-class decision value**. It is not a tier. It is not SAFE.
It is not ALLOW.

---

## Fields

| field | type | meaning |
|-------|------|---------|
| decision | string | ALLOW / REVIEW / BLOCK / UNKNOWN |
| capability | optional string | the resolved capability, if any |
| tier | optional int | the capability's tier, if known |
| resolution_method | optional string | how the capability was resolved |
| confidence | float | descriptive confidence (not authority) |
| taint | string | clean / tainted |
| authority | optional string | who authorized (not yet wired) |
| approval | string | not_required / required_not_satisfied / required_satisfied |
| policy | optional string | which policy governed (not yet wired) |
| reason | string | human-readable reason |
| evidence | tuple of strings | evidence recorded for this decision |
| alternatives | tuple of strings | alternative capabilities considered |
| conflicts | tuple of strings | conflicts detected (not yet wired) |
| verification_required | bool | whether verification is required |

Fields that are not yet wired into the live path are present as explicit `None`
values. They are not omitted — omitting them would let the contract drift as
later phases add them.

---

## Constructors

- `from_capability_resolution(resolution, decision=...)` — builds a decision
  from a capability resolution. The decision value is supplied by the caller
  (typically the gate or the policy engine), not by the resolver.
- `unknown_decision(...)` — convenience constructor for an UNKNOWN decision.

---

## What this object does NOT record

- It does not record the outcome of execution.
- It does not record the verification result.
- It does not record the audit hash.
- It does not record authority, approval, policy, or conflicts that haven't
  been wired yet.

Those are recorded elsewhere (the receipt, the evidence spine, the audit chain)
or will be added later.

---

## Relationship to the resolver

The resolver produces a `CapabilityResolution`. That is a *proposal*. The
canonical decision object is the *decision*. The bridge is
`from_capability_resolution(resolution, decision=...)`.

The resolver does not decide. The gate (or the policy engine) decides.

---

## Relationship to the gate

The gate produces a `GateVerdict` today. That will eventually be translated
into a `GovernanceDecision` so the receipt and shadow mode record one shape
regardless of which subsystem made the decision.

---

## Relationship to the receipt

The receipt will later record the full decision chain:

```
REQUESTED
NORMALIZED
CAPABILITY RESOLVED
RISK CLASSIFIED
TAINT ASSESSED
AUTHORITY CHECKED
APPROVAL CHECKED
TOOL AUTHORIZED
EXECUTED
OBSERVED
VERIFIED
AUDITED
```

The `GovernanceDecision` is one vertebra in that chain — typically the
CAPABILITY RESOLVED / RISK CLASSIFIED / AUTHORITY CHECKED vertebra.

---

## What this is NOT

- It is not the receipt.
- It is not the evidence spine.
- It is not the audit chain.
- It is not yet wired into the live execution path — that comes later.
- It is not versioned or change-controlled yet — that comes later.

---

## Open questions for later phases

- How does the gate translate a `GateVerdict` into a `GovernanceDecision`?
- How does the receipt record the decision chain?
- How does shadow mode record old vs new decisions using this object?
- How do authority, approval, policy, and conflicts get wired in?
