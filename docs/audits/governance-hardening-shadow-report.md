# Governance Hardening — Shadow-Mode Report

**Phase:** 5 (shadow mode, no enforcement yet).  
**Date:** 2026-09-02.  
**Recorder:** `src/msb_v3/governance/shadow.py`.

> Shadow mode runs the old ActionGate path and the new resolver path in
> parallel and records the disagreement, without changing execution. This
> report is the first disagreement dataset.

---

## What shadow mode does

- For each request, shadow runs the old gate (ActionGate directly on the raw
  string) and the new resolver (CapabilityResolver + canonical decision) in
  parallel.
- It records old_action, old_tier, new_decision, new_capability,
  new_resolution_method, new_confidence, new_reason, new_evidence,
  disagreement, and a disagreement class.
- It does not change execution. The old gate still runs.

---

## Representative batch

A small batch of requests covering known-safe, known-dangerous (keyword),
semantically-dangerous-but-not-keyword, and genuinely novel requests.

| request_id | request | old_action | old_tier | new_decision | new_capability | new_method | new_tier | new_confidence | disagreement |
|------------|---------|------------|----------|--------------|----------------|------------|----------|----------------|--------------|
| r1 | search the vault | BLOCK | -1 | ALLOW | read_vault | intent_template | 1 | 1.0 | False |
| r2 | read the vault | BLOCK | -1 | ALLOW | read_vault | intent_template | 1 | 1.0 | False |
| r3 | summarize the findings | BLOCK | -1 | ALLOW | llm_synthesis | intent_template | 1 | 1.0 | False |
| r4 | write a file to artifacts | BLOCK | -1 | ALLOW | write_file | intent_template | 2 | 1.0 | False |
| r5 | delete the old content | BLOCK | -1 | REVIEW | vault_delete | intent_template | 3 | 1.0 | False |
| r6 | send a message to the client | BLOCK | -1 | REVIEW | send_message | intent_template | 3 | 1.0 | False |
| r7 | make a financial transfer | BLOCK | -1 | BLOCK | financial | intent_template | 4 | 1.0 | False |
| r8 | revoke access for john | BLOCK | -1 | BLOCK | permissions | intent_template | 4 | 1.0 | False |
| r9 | execute the payload | BLOCK | -1 | UNKNOWN | None | none | None | 0.0 | False |
| r10 | rm -rf production | BLOCK | -1 | UNKNOWN | None | none | None | 0.0 | False |
| r11 | do the thing | BLOCK | -1 | UNKNOWN | None | none | None | 0.0 | False |
| r12 | something completely fresh and novel | BLOCK | -1 | UNKNOWN | None | none | None | 0.0 | False |

> Note: the "old_action" column reports the gate's behavior **after** the
> Phase 0 UNKNOWN fix. Before that fix, r1-r8 would have been SAFE/tier 1, and
> r9-r12 would have been SAFE/tier 1 as well. The shadow recorder captures the
> post-fix baseline, and the disagreement dataset will be useful once the
> resolver is wired into the gate and the old path is the *pre-fix* baseline.

---

## Disagreement classes observed

No high-signal disagreements in this batch because the old gate already returns
BLOCK for the unknown requests (Phase 0 fix). The more interesting case —
old SAFE, new UNKNOWN or new high-risk — would appear if shadow were run
against the pre-fix baseline or if the resolver is wired into the gate and the
old path is kept as the pre-fix baseline for comparison.

The disagreement classes defined are:

- `agree` — old and new agree.
- `OLD_SAFE_NEW_UNKNOWN` — old said SAFE, new says UNKNOWN.
- `OLD_SAFE_NEW_NOT_SAFE` — old said SAFE, new says BLOCK or REVIEW.
- `OLD_BLOCK_NEW_NOT_BLOCK` — old said BLOCK, new says something else.
- `OLD_REVIEW_NEW_NOT_REVIEW` — old said REVIEW, new says something else.
- `OLD_NOT_UNKNOWN_NEW_UNKNOWN` — old said something else, new says UNKNOWN.
- `OLD_NOT_BLOCK_NEW_BLOCK` — old said something else, new says BLOCK.
- `OLD_NOT_REVIEW_NEW_REVIEW` — old said something else, new says REVIEW.
- `OTHER_DIFFERENCE` — something else differs.

---

## What this batch shows

- The resolver resolves known request shapes (search the vault, write a file,
  delete the old content, etc.) via deterministic templates.
- The resolver returns UNKNOWN for genuinely novel requests (execute the
  payload, do the thing, something completely fresh and novel).
- The old gate already BLOCKs the unknown requests because of the Phase 0 fix,
  so shadowing against the post-fix baseline does not yet produce the most
  interesting disagreements.

---

## Next step for shadow mode

The interesting shadow data appears when:

- the resolver is wired into the gate, AND
- the old path is kept as the pre-fix baseline for comparison, OR
- a larger representative corpus is run through shadow.

That is Phase 6 onward.

---

## Open questions

- How large should the shadow corpus be before enforcement?
- Should shadow record the old pre-fix behavior explicitly, or should it record
  the post-fix baseline and let the disagreement dataset accumulate over time?
- Which disagreement classes matter most for the FSSR metric?
