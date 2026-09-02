# Tool Manifest v1

**Status:** Phase 2 deliverable, seeded from current mapping.  
**Source:** `src/msb_v3/governance/tool_manifest.py`.  
**Seed source:** `TOOL_CAPABILITY` in `msb_v3.agent.safety`.

---

## What this is

One authoritative inventory of governed tool manifests. Each manifest declares
what capability(ies) a tool may exercise, its maximum tier, and whether it is
enabled.

The intent is simple: **a tool cannot exercise a capability it does not
declare.**

---

## Bridge invariant (Phase 2)

During Phase 2 the manifest registry is a *mirror* of the current
`TOOL_CAPABILITY` mapping:

- Every tool in `TOOL_CAPABILITY` has a manifest.
- The manifest registry contains no extra tools yet.
- `manifest_registry_matches_current_tool_mapping(registry)` is true.

That invariant is tested, not assumed.

---

## Initial manifests

| tool_id | capabilities | maximum_tier | approval_required | taint_policy | side_effects | enabled |
|---------|--------------|--------------|-------------------|--------------|--------------|---------|
| search_query | read_vault | 1 | False | review | read_only | True |
| vault_read | read_vault | 1 | False | review | read_only | True |
| chat | llm_synthesis | 1 | False | review | read_only | True |
| vault_write | write_file | 2 | False | review | local_side_effect | True |

---

## Unknown tools

A tool with no manifest resolves to `None`. It cannot exercise any capability
through the manifest registry.

That is the UNKNOWN-tool invariant: an undeclared tool does not inherit SAFE.

---

## Manifest mismatch

A manifest may exercise only the capabilities it declares. If a call wants to
exercise a capability outside the manifest, the manifest says no.

That mismatch is detectable in tests today. It will be enforced in the
executor/gate in a later phase.

---

## What this is NOT

- It is not yet enforced at execution time — that comes later.
- It is not a semantic model of every possible tool — only the governed tools
  MSB-v3 currently exercises.
- It is not yet versioned or change-controlled — that comes later.

---

## Open questions for later phases

- How does the manifest become the upstream of tool authorization in the gate?
- How are new tools added without silently inheriting SAFE?
- How does manifest mismatch propagate into the canonical decision object and
  the evidence receipt?
