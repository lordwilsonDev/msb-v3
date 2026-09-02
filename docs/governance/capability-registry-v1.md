# Capability Registry v1

**Status:** Phase 1 deliverable, seeded from current tables.  
**Source:** `src/msb_v3/governance/capability_registry.py`.  
**Seed source:** `RISK_TIERS` + `TOOL_CAPABILITY` in `msb_v3.agent.safety`.

---

## What this is

One authoritative inventory of registered capabilities. If a capability is not
in this registry, it is **UNKNOWN** — not SAFE, not Tier 1, not ALLOW.

This is the structural source of truth the hardening work builds on. The gate
still uses `RISK_TIERS` directly today; later phases make the registry the
upstream of the gate.

---

## Bridge invariant (Phase 1)

During Phase 1 the registry is a *mirror* of the current `RISK_TIERS` table.
That means:

- Every capability in `RISK_TIERS` is in the registry.
- The registry contains no extra capabilities yet.
- `registry_matches_current_tables(registry)` is true.

That invariant is tested, not assumed.

---

## Initial contents

| capability_id | domain | tier | side_effect_class | reversibility | approval_required | taint_policy |
|---------------|--------|------|-------------------|---------------|-------------------|--------------|
| read_vault | vault | 1 | read_only | reversible | False | review |
| llm_synthesis | llm_synthesis | 1 | read_only | reversible | False | review |
| web_search | web_search | 1 | read_only | reversible | False | review |
| write_file | file | 2 | local_side_effect | less_recoverable | False | review |
| vault_delete | deletion | 3 | local_side_effect | irreversible | True | review |
| send_message | communication | 3 | external_side_effect | less_recoverable | True | review |
| financial | financial | 4 | external_side_effect | irreversible | True | review |
| permissions | permissions | 4 | external_side_effect | irreversible | True | review |

---

## Interpretation rules

- **tier** is still the severity axis the gate uses today.
- **side_effect_class** is a first cut at the consequence axis: `read_only`,
  `local_side_effect`, `external_side_effect`.
- **reversibility** is a first cut at recoverability: `reversible`,
  `less_recoverable`, `irreversible`.
- **approval_required** mirrors the existing tier-based refusal logic: tier 3/4
  capabilities require approval, tier 4 also BLOCKs.
- **taint_policy** is the current A8 behavior: tainted writes escalate to
  REVIEW.

These fields are descriptive. They are not yet the full policy engine. The full
policy engine comes later, once the registry, the tool manifests, and the
resolver exist.

---

## Unknown capabilities

UNKNOWN capabilities are **not** in this registry. They resolve to `None`.

That is the whole point of Phase 1: make the unknown state explicit at the
source-of-truth level rather than leaving it as "not in RISK_TIERS → tier 1".

---

## Relationship to ToolManifest

TOOL_CAPABILITY says which capability each existing tool exercises. That
mapping is being moved into ToolManifest in Phase 2. For now it stays in
`safety.py`, and the registry just records the capability side of the
relationship.

---

## What this is NOT

- It is not a semantic model of every possible capability in the world.
- It is not authoritative for execution — it describes capabilities, it does
  not authorize them.
- It is not yet versioned or change-controlled — that comes later.

---

## Open questions for later phases

- How does the registry become the upstream of `ActionGate.tier_of()`?
- How are new capabilities added without silently inheriting Tier 1?
- What is the exact schema for a capability beyond this v1 description?
- How does the registry interact with the deterministic resolver and the
  canonical decision object?
