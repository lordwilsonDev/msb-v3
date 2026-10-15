# MSB-v3 Decision Ledger

This is an architectural archaeology record: why important mechanisms exist, what
evidence supports them, and where their guarantees stop.

## D-001 — UNKNOWN must not inherit SAFE

Evidence: DOCUMENTED + IMPLEMENTED + TEST + SHADOW/FORENSIC.

Problem: an unregistered capability previously inherited a safe tier.

Decision: explicit UNKNOWN semantics, capability registry, resolver, and
GovernanceDecision.

Mechanism: unknown capability is not assigned a safe tier; default disposition
is non-execution.

Why: absence of authority is not evidence of safety.

Limit: V1 resolution is intentionally small, so many inputs remain UNKNOWN.

## D-002 — Capability is the unit of authority

Evidence: DOCUMENTED + IMPLEMENTED + TEST.

Tools declare capabilities. Agents receive grants. The gate checks those grants.

Consequence: a prompt, tool name, or model output cannot create authority by itself.

## D-003 — Model proposes; policy authorizes

Evidence: DOCUMENTED + IMPLEMENTED.

The resolver and MoIE can provide evidence or risk signals. Final authorization
comes from deterministic policy/gating.

## D-004 — Local-first is a compute boundary

Evidence: IMPLEMENTED + TEST + OBSERVED.

The remote DeepSeek/frontier seam was retired in September 2026. Oversized calls
stay local and the audit reason records the degradation.

## D-005 — Providers are workers behind a seam

Evidence: ADR + IMPLEMENTED + TEST.

Provider selection separates how work is done from governance. Configuration/plugin
registration reduces central wiring pressure.

## D-006 — CLI provider isolation risk is accepted, not hidden

Evidence: ADR + IMPLEMENTED + ADVERSARIAL.

CLI agents run under the operator account. Worktrees, capabilities, timeouts,
output bounds, environment allowlisting, and no-implicit-capability rules bound
risk, but this is not a true sandbox.

Reversal conditions include untrusted network exposure, a demonstrated escape,
multi-tenant deployment, or materially tighter governance.

## D-007 — Evidence deserves its own substrate

Evidence: ADR + IMPLEMENTED + ADVERSARIAL.

The Evidence Spine stores decision/execution/result/verification records. UAC and
msb_ledger provide append-only chain evidence.

Why: normal application logs do not provide equivalent provenance guarantees.

## D-008 — Merkle receipts and signed anchors

Evidence: IMPLEMENTED + ADVERSARIAL.

They enable compact proof of inclusion and a durable external trust point.

## D-009 — Rerun and log inference are different evidence classes

Evidence: IMPLEMENTED.

Receipts distinguish basis=rerun, basis=decision-only, and
basis=inferred-from-logs.

Why: calling reconstruction a rerun inflates certainty.

## D-010 — Do not build another orchestrator beside the canonical loop

Evidence: PROPOSED/BLUEPRINT + current architecture.

The 2026-09-25 control-plane blueprint explicitly chooses convergence and reuse of
existing mission, factory, provider, and governance pieces.

The new archaeology harness follows that rule by remaining read-only.

## D-011 — Remove architecture theater

Evidence: IMPLEMENTED + DOCUMENTED.

The previous meta-cognitive planner was flattened after forensic review found it
generated static ceremony instead of goal-dependent planning. Real planning is in
agent.planner.

## D-012 — Failures must be visible

Evidence: IMPLEMENTED + TEST.

Examples include visible degraded chat results, fail-closed gate exceptions,
logged exception failures, stale-state SSE closure, and audit-write error
surfacing.

## D-013 — Portability is correctness

Evidence: IMPLEMENTED + OBSERVED.

The project has a portability gate because supervisor environments have previously
differed from interactive shells. One incident involved bare python/python3 names
resolving differently under launchd.

## D-014 — Storage is part of system integrity

Evidence: IMPLEMENTED + OBSERVED.

Qdrant working-directory behavior caused a real storage trap. The project responded
with explicit working-directory control, backups, restore drills, checksum checks,
and watchdogs.

## D-015 — Freeze the canonical path before expansion

Evidence: RELEASE + BLUEPRINT.

The v0.3.0 release froze the live path and parked stronger sandboxing,
multimodal, distributed mesh, tenant isolation, and autonomous evolution.

## D-016 — One measured mission before workforce scaling

Evidence: PROPOSED.

The 2026-09-25 control-plane blueprint defines one bounded todo/board mission as
the first full end-to-end productization experiment.

That is a design experiment, not evidence that the complete worker workforce
already exists.

## Maintenance format

For every future major decision record:
ID
problem
decision
mechanism
evidence level
tests
observed result
limitations
reversal condition
supersedes / superseded-by

Never erase history. Mark decisions superseded instead.

## Recurring engineering pattern

OBSERVATION
  -> forensic root cause
  -> narrow correction
  -> adversarial/regression test
  -> evidence-linked documentation
  -> freeze
