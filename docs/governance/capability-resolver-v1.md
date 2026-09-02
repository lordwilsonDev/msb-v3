# Capability Resolver v1

**Status:** Phase 3 deliverable, deterministic, no model authority.  
**Source:** `src/msb_v3/governance/capability_resolver.py`.

---

## What this is

A deterministic step that turns a natural-language request into a capability
decision *before* the gate. The system is no longer gating on a raw string; it
resolves the request first, then authorizes.

This is the bridge between "I have a request" and "I have a capability to
govern."

---

## Resolution sources (priority order)

1. **Tool manifest** — if the request names a known tool, the tool's declared
   capabilities are the strongest deterministic signal.
2. **Capability registry** — if the request names a known capability by name.
3. **Deterministic intent templates** — a small, explicit set of request shapes
   that map to capabilities.
4. **Otherwise UNKNOWN.**

The existing MoIE keyword/pattern signal is *not* folded into V1 authority.
It becomes one input a caller may add as evidence, not a resolution source.

---

## Deterministic templates (V1)

The template set is intentionally tiny. If a request doesn't match a template,
the resolver does not guess.

| template probes | resolved capability |
|-----------------|----------------------|
| search the vault | read_vault |
| read the vault | read_vault |
| summarize / synthesize / write a brief / write a note | llm_synthesis |
| search the web / web search / look up online | web_search |
| write a file / write the file / create a file | write_file |
| delete / remove / wipe / destroy / nuke | vault_delete |
| send / message / notify / email | send_message |
| financial / payment / transfer / purchase | financial |
| permission / access / grant / revoke | permissions |

These are *signals*, not magic. They are auditable and can be expanded later as
the hardening work demands.

---

## What the resolver returns

A `CapabilityResolution`:

```python
{
    "resolved_capability": Optional[str],
    "resolution_method": str,        # tool_manifest | capability_name | intent_template | none
    "confidence": float,             # 1.0 when resolved by an explicit source, 0.0 when UNKNOWN
    "alternatives": Tuple[str, ...],
    "ambiguous": bool,
    "risk_tier": Optional[int],
    "reason": str,
    "evidence": Tuple[str, ...],
}
```

---

## What the resolver does NOT do

- It does **not** authorize execution.
- It does **not** make the model the authority.
- It does **not** resolve every possible request.
- It does **not** treat confidence as permission. `confidence=1.0` means the
  request matched an explicit source; it does not mean the request is safe.
- It does **not** fold MoIE verdicts into authority in V1.

---

## UNKNOWN behavior

If no source resolves the request, the resolver returns:

```python
{
    "resolved_capability": None,
    "resolution_method": "none",
    "confidence": 0.0,
    "ambiguous": True,
    "risk_tier": None,
    "reason": "no registered capability resolved this request",
    "evidence": ("no registered source resolved the request",),
}
```

UNKNOWN is a real governance state. It is not SAFE. It is not Tier 1. The gate
receives a capability decision, and the gate's UNKNOWN disposition applies.

---

## Relationship to the gate

The resolver proposes a capability. The gate authorizes (or refuses) based on
that capability. If the resolver returns UNKNOWN, the gate's UNKNOWN disposition
applies:

- UNKNOWN + tainted → REVIEW
- UNKNOWN + untainted → BLOCK

That is the Invariant-002 path — unknown consequential capability cannot execute.

---

## What this is NOT

- It is not a semantic model of every possible request in the world.
- It is not a replacement for the keyword pre-filter — it complements it.
- It is not yet wired into the live execution path — that comes later.
- It is not versioned or change-controlled yet — that comes later.

---

## Open questions for later phases

- How does the resolver expand beyond the tiny template set without introducing
  model authority?
- How does the resolver handle ambiguous requests with multiple candidate
  capabilities?
- How does the resolver interact with DAG capability closure?
- How does the resolver's output flow into the canonical decision object and
  the evidence receipt?
