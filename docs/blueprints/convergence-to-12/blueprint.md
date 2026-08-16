# MSB v3: Convergence-to-12 Blueprint

> **Source:** pasted by Wilson, 2026-08-16. Preserved verbatim as the governing
> plan for the v3 convergence program. Milestone status is tracked in
> [`MILESTONES.md`](MILESTONES.md); scope and non-goals in
> [`v3-contract.md`](v3-contract.md); the unfinished-surface classification in
> [`surface-inventory.md`](surface-inventory.md).

Prepared for: Wilson
Purpose: Move MSB v3 from an unusually strong solo engineering foundation to a finished, validated, independently useful platform.
Current assessment: 8.5/10 for solo-builder seriousness; approximately 6/10 for demonstrated production readiness.
Operating principle: Do not expand the architecture until the core loop is proven.

**A 12/10 MSB v3 is not the system with the most subsystems. It is the smallest coherent system that can govern, execute, explain, recover, and improve real work under conditions you do not fully control.**

## 1. Target state

The target is a narrow, local-first governed agent runtime with one undeniable end-to-end workflow. It should accept a real task, plan and execute bounded actions, enforce governance before action, record evidence, expose useful telemetry, recover from model or tool failure, and produce an artifact that a second person can understand and trust.

The target is deliberately smaller than the current architectural surface. Subsystems that do not support the proven core path must be implemented, moved to an explicitly labeled experimental area, or removed from the shipping surface.

| Target property | Definition of done |
|---|---|
| Coherent core | One documented path owns the primary user outcome from request to verified result. |
| Governed execution | Guard decisions occur in the real autonomous loop, before consequential actions. |
| Evidence | Every meaningful decision and action has an inspectable record with outcome and failure context. |
| Safe failure | Model, tool, timeout, permission, malformed-input, and partial-execution failures stop or recover predictably. |
| Diverse review | The builder and reviewer are materially independent, and the reviewer catches seeded defects. |
| Factory dogfooding | At least one real MSB change is built, reviewed, verified, and merged through the factory. |
| External legibility | A technically capable outsider can understand the architecture, run the happy path, and interpret the evidence. |
| Real utility | Thirty to sixty days of personal use produce measurable time saved, errors prevented, or quality improved. |

## 2. The governing rules

**Rule 1: Every item has only three possible destinations.** Each stub, orphan module, deferred phase, weakly wired component, or speculative interface must be wired, cut, or parked in experiments. "Keep for later" is not a fourth category unless it has an owner, a dated decision, and an explicit reason to remain in the repository.

**Rule 2: Milestones are evidence gates, not calendar promises.** The timeboxes below are planning estimates for a solo builder working consistently. A milestone is complete only when its exit evidence exists. If the work takes longer, the milestone remains open; the schedule does not redefine done.

**Rule 3: Freeze before extending.** During Milestones 0–3, no new subsystem is allowed unless it directly closes a failure discovered in the core path. New ideas go into a dated parking-lot record with a proposed value, dependency, and trigger for reconsideration.

**Rule 4: Optimize for leverage, not subsystem count.** The scoreboard is the number of reliable, valuable loops completed—not source lines, package count, model count, or architectural vocabulary.

## 3. Milestone roadmap

| Milestone | Name | Primary outcome | Suggested window |
|---|---|---|---|
| M0 | Scope Lock and Baseline | A frozen v3 contract and clean inventory of unfinished surface area | 2–3 days |
| M1 | Core Loop Selection | One canonical workflow chosen, specified, and instrumented | 3–5 days |
| M2 | Governance in the Loop | Guard and evidence enforcement operate on the live path | 5–10 days |
| M3 | Shipping-Surface Convergence | Stubs, orphans, and deferred phases are resolved or isolated | 3–7 days |
| M4 | Factory Dogfood | MSB builds, reviews, verifies, and lands a real change through itself | 5–10 days |
| M5 | Reliability and Adversarial Proof | The core survives deliberate failure and abuse tests | 7–14 days |
| M6 | Personal Production Trial | Continuous real-world use generates measurable operating evidence | 30–60 days |
| M7 | Independent User Validation | An outsider can use and evaluate the system without handholding | 2–4 weeks |
| M8 | Public 12/10 Release | The project is packaged as a defensible platform and case study | 1–2 weeks |

## 4. Milestone specifications

### M0 — Scope Lock and Baseline

Objective: Convert MSB v3 from a broad platform into a controlled release candidate.

Create a one-page v3 contract that states the target user, the primary problem, the canonical workflow, supported models and tools, safety boundaries, evidence guarantees, and explicit non-goals. Inventory every occurrence of stub, NotImplementedError, Phase 2, TODO, orphan module, placeholder metric, and undocumented external dependency. Classify each as wire, cut, or park.

Exit criteria: v3 contract exists (committed document with scope, non-goals, and release language); surface inventory is complete (checked-in table linking each unfinished item to wire/cut/park); baseline is reproducible (test, type, lint, security, and coverage commands run from a clean checkout); expansion freeze is active (new work outside the contract requires a written exception).

> Failure condition: If the primary user outcome cannot be stated in one sentence, do not start M1. Narrow the project again.

### M1 — Core Loop Selection

Objective: Select the single path that will carry the proof burden for v3.

Choose one workflow that is useful to you and exercises the architecture. A good candidate should require model reasoning, tool or data interaction, governance, evidence recording, verification, and a recoverable failure mode. Write the path as a state machine rather than a narrative. The minimum state sequence: `Request → classify → plan → authorize → execute → observe → verify → record → report or recover`.

Define the contracts at every boundary: inputs, outputs, permissions, timeouts, retry rules, evidence fields, and terminal states. Add telemetry before adding more capability.

Exit criteria: canonical workflow selected (one named workflow and one deliberately rejected alternative); state machine documented; golden fixtures exist; baseline observability exists (a run identifier links decisions, actions, tool calls, and final result).

### M2 — Governance in the Loop

Objective: Make governance operational rather than architectural.

Wire the Guard into the actual autonomous execution path before consequential actions. Ensure that a denied, uncertain, expired, malformed, or unavailable authorization cannot silently become an allowed action. Make the evidence record append-only or otherwise tamper-evident within the system's intended threat model. Test the negative paths first. The critical demonstration is that the runtime refuses an unsafe or unauthorized action, records the reason, and returns a useful recovery or escalation outcome.

Exit criteria: guard is on the live path (integration test proves every consequential action passes through governance); denials are fail-closed; evidence is complete (a reviewer can reconstruct the decision, policy, action, result, and failure context); governance is observable (metrics distinguish allowed, denied, indeterminate, failed, and stub states); regression protection exists.

> Release decision: If governance cannot be enforced centrally, reduce the supported action surface until it can.

### M3 — Shipping-Surface Convergence

Objective: Remove the gap between what the repository claims and what it can do.

Resolve the multimodal interfaces and any other live stubs. Preferred order: implement a narrow useful contract, test it, document its limits. If an interface does not support the core path, remove it from the shipping surface and preserve the idea only in `experiments/` or an architecture note. Wire or cut the governance Phase 2 work. Perform a second pass over orphaned modules, unused configuration, misleading names, metrics that count placeholders, and documentation that describes aspirational behavior as current capability.

Exit criteria: no dateless shipping stubs; no misleading claims (README, metrics, architecture docs match runtime behavior); core surface is smaller or clearer (before/after inventory); full gates remain green.

### M4 — Factory Dogfood

Objective: Prove that the self-building factory is useful on its own codebase.

Select a small but real change. Have the factory generate or scaffold the change, run the deterministic review, invoke a genuinely diverse model expert that does not use the same provider or model family as the builder, execute verification, and produce a review artifact. Merge only through the factory path. Seed at least one known defect or tricky edge case into a test branch to confirm that the reviewer can detect something nontrivial.

Exit criteria: full factory path completes (a run artifact links request, generated change, review, verification, and merge decision); diverse review is real (reviewer provider/model identity and independence rationale recorded); reviewer catches a seeded issue; human override is explicit; no abandoned worktrees remain.

### M5 — Reliability and Adversarial Proof

Objective: Establish that the core path behaves safely when reality is uncooperative.

Build a failure matrix covering model unavailability, invalid model output, tool timeout, permission denial, duplicate request, partial completion, stale evidence, corrupted state, retry exhaustion, prompt injection, and conflicting instructions. For every case, define the expected terminal state and evidence record. Run a soak test using realistic workloads.

Exit criteria: failure matrix is implemented; no silent unsafe continuation; soak run is completed; recovery is bounded; security cases are covered.

### M6 — Personal Production Trial

Objective: Determine whether MSB improves actual work rather than merely demonstrating engineering capability.

Use MSB v3 for one or two recurring workflows over 30–60 days. Keep a lightweight operating ledger: task type, baseline time, MSB time, intervention required, outcome quality, failure mode, and whether the evidence record was useful. Do not only record wins.

Exit criteria: real usage is sustained (≥30 days or meaningful volume); value is measurable; failure burden is known; the product is refined from use.

> Decision gate: Continue toward external users only if the system produces repeatable value without becoming more work to supervise than the work it replaces.

### M7 — Independent User Validation

Objective: Test whether MSB is legible and useful outside the builder's mental model.

Choose two or three technically capable users. Give them a constrained task, a clean setup, a short operating guide, and access to the evidence and failure reports. Observe without rescuing them immediately.

Exit criteria: users complete the target task (at least one without live intervention); feedback is behavioral; setup is reproducible; trust model is understandable; product scope is revised.

### M8 — Public 12/10 Release

Objective: Package the work so its seriousness is visible and defensible.

Create a concise public-quality demonstration, architecture diagram, threat and failure model, benchmark report, and case study from the personal production trial. Clearly separate implemented, experimental, and future capabilities. Publish only after secrets, data, and operational assumptions have been reviewed.

Exit criteria: capability claims are auditable (each major claim links to code, test, run artifact, or measured result); demo is reproducible; results are quantified; limitations are prominent; the project is declared done (a dated v3 release decision records what is frozen and what belongs in v4).

## 5. Operating cadence

Weekly cycle built around evidence rather than volume: begin with a short convergence review, choose one milestone outcome, implement only the smallest change that advances it, run the real gates, end with a written decision. Keep a visible ledger with four columns: claim, evidence, gap, next action.

- **Daily:** one core-path run or one clearly scoped implementation step. Record failures immediately.
- **Weekly:** review unfinished surface area, metrics, intervention burden, expansion requests.
- **At every merge:** run the full relevant gates and attach evidence to the change.
- **Every milestone:** a short decision memo (achieved, not achieved, evidence, risks, next gate).
- **Monthly:** recalculate whether MSB is saving time and reducing errors in real work.

## 6. The scoreboard

| Metric | Why it matters | Suggested target before M8 |
|---|---|---|
| Canonical-path completion rate | Measures whether the system works as a whole | ≥ 90% on supported cases |
| Unsafe-action escape rate | Measures governance integrity | 0 observed escapes |
| Evidence completeness | Measures auditability | ≥ 98% of meaningful events |
| Human intervention rate | Measures operational burden | Decreasing each release |
| Recovery success rate | Measures resilience | ≥ 80% of defined recoverable failures |
| Factory defect-detection rate | Measures self-building leverage | Demonstrated on seeded defects; improving thereafter |
| Median setup time for an outsider | Measures independent usability | Under one focused session |
| Personal time saved | Measures real utility | Positive net savings after supervision |
| External user task success | Measures value beyond the builder | At least one independent successful user |

## 7. Explicitly out of scope until after M7

Do not add more modalities, more agent personas, more orchestration layers, more model providers, generalized autonomous behavior, polished dashboards, or broad platform abstractions unless a milestone's evidence proves that the missing capability is blocking the canonical path. These may be good v4 ideas. They are not valid reasons to delay convergence in v3.

## 8. The 12/10 threshold

MSB v3 reaches the "12/10 solo builder" category when the following statement becomes true:

> MSB v3 is a narrow, governed, local-first runtime that I use continuously for real work; it completes a valuable workflow reliably; it fails safely and explains itself; it can build and review a real change through its own factory; an independent user can operate it; and the improvement is demonstrated with evidence rather than architecture claims.

That is the finish line. More code is optional. Proof is not.

## Immediate next seven actions

1. Write and commit the one-page v3 contract.
2. Generate the complete wire/cut/park inventory.
3. Choose one canonical workflow and express it as a state machine.
4. Wire governance into that path before adding capability.
5. Resolve or isolate every shipping-surface stub.
6. Register and test the independent model reviewer.
7. Run one real change through the factory and publish the evidence artifact.

Once those seven actions are complete, MSB v3 will have moved from an impressive system inventory to a demonstrated system.
