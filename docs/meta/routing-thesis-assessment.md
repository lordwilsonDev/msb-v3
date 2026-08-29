# Assessment — the routing / decomposition idea

**Date:** 2026-08-29
**Context:** A proposal circulated to reframe MSB-v3 as a "governed AI construction
system" — an architectural decomposer turns a big goal into bounded jobs, a skill
router matches each job to a capability contract, and model selection becomes
secondary to "what kind of work is this." Prompted by the Qwen-8B `_child_env`
result (a compiled spec executed correctly by an 8B). This is the engineering
assessment of that proposal.

---

## Verdict

The reframe is correct and it is the **defensible** form of the thesis:

> Model capability is one variable. For large engineering tasks, decomposition,
> context management, skill routing, execution control, and verification can
> become the dominant bottleneck.

Keep exactly that phrasing — it does not over-claim, and the `_child_env` result
supports it as **one data point, not proof of scale.**

Three corrections before building from the proposal.

---

## 1. This is the Meta-System, re-derived — not a new direction

The proposed "missing recursive work-management layer"
(`GOAL → UNDERSTAND → GRAPH → DECOMPOSE → ROUTE → EXECUTE → VERIFY → REPAIR →
REPLAN → INTEGRATE`) is META-1..8, already built at roughly 40–60% in
`src/msb_v3/meta/`:

| Component | File |
|---|---|
| topological ordering, dependency gating, cycle detection | `meta/scheduler.py` |
| render → model → parse → write → run_checks → correction-retry (≤3) → BuildOutcome | `meta/loop.py` |
| classify_check + verdict_from_checks + run_checks | `meta/verify.py` |
| render_prompt / call_ollama / parse_worker_response | `meta/worker.py` |
| OutcomeLedger (execution recording → probability feed) | `meta/outcome/ledger.py` |
| AdaptiveOptimizer (routing from verified outcomes) | `meta/adaptive/` |
| MultiWorkerBenchmark (cross-worker comparison) | `meta/benchmark/` |
| MetaTask, MSL, TaskState, WorkerResult, VerificationResult, FailureRecord | `meta/contracts.py` |
| `SkillBinding` maps skill → (provider tuple) | `plei/engineering/capability_graph.py` |
| WorkPlan / WorkPlanStep with `capabilities_required` | `plei/harness/work_plan.py` |
| "deterministic decomposition" | `factory/planner.py` |

**Action:** diff the proposal against `src/msb_v3/meta/` before treating it as
greenfield. What it genuinely adds:

1. **Six typed capability-contract skills** — `project-understanding`,
   `architect`, `research`, `implementation`, `debug`, `verification` — each with
   a strict input schema, output schema, and a conformance suite, the way
   `ProviderContract v1` has one.
2. **An architectural decomposer** as a distinct component *above*
   `factory/planner` (project → phases → jobs, vs. today's issue → steps).

Everything else is already scaffolded. Rebuilding a decomposer that exists is the
build-before-converge pattern with a diagram attached.

---

## 2. The proposal skips the step that decides whether any of this works: verification hardness

Every box in the flow collapses to `VERIFY → PASS/FAIL`. That box is where the
architecture succeeds or fails, and the proposal does not engage with it.

- `_child_env` passed because a **hidden pytest** existed — machine-checkable,
  binary, cheap to author.
- For "implement feature X", "produce an architecture plan", or `render_objective`
  (prose), what is the verifier?
- If the verifier is another LLM, the model-capability bottleneck has moved to the
  verification layer, where it is **less** reliable, not more.
- Already documented in the dsh experiments: *"the fuzzy-verification wall — T5
  (`render_objective`, prose, no ground-truth test) was never reached."*

Writing a machine-checkable acceptance test for each job is itself a hard job —
frequently harder than the implementation it gates — and the proposal's own
decomposition (`JOB-001..010`) does not include "author the checker" as a job.

**Failure mode to expect:** 15 implementation jobs that each individually "pass" a
weak verifier and do not integrate.

**Rule:** do not start job #4 (implement) on anything until job #7's checker — a
real, runnable test — exists.

---

## 3. The proposal asserts past the economics

The thesis wins only if:

```
cost(strong-model translation + verifier authoring + orchestration + retries)
    <  cost(strong model just building the subsystem directly)
```

Inputs to that inequality:

- 8B first-pass rate on well-specified jobs is documented in the literature as
  "high variance … massive failures on some tasks" — not a fixed 90%.
- The north-star example is 47 jobs, each = one compile + one verify + up to 3
  retries.
- Strong-model tokens are spent on decomposition, translation, and verification of
  every job.

This is the blueprint's §18 killer experiment. It has not been run. Until it is,
"amplify a weak model with a rigorous harness" is a hypothesis, not a result.

---

## Next experiment (replaces "give Qwen a subsystem blueprint")

Do **not** hand Qwen a whole subsystem yet.

1. Pick **one** real subsystem that decomposes to 4–6 jobs.
2. By hand, write the machine-checkable acceptance test for **every** job first.
   If that is expensive or impossible for some jobs, **stop** — that is the
   finding: the architecture is gated on verification authoring, not routing.
3. Run the job set through `meta/loop.py`.
4. Measure, per job and in total: completion rate, rework/retry rate, test pass
   rate, contract violations, tool errors, context tokens used, human
   interventions, and **total token cost split strong-model vs 8B.**
5. Compare total cost against the strong model building the same subsystem
   directly, once.

That single comparison tells you whether the architecture scales. One passing
function does not.

---

## A second failure class, observed 2026-08-29

When the `_child_env` assessment was fed back through `dsh --profile headless`,
Qwen-8B made the **correct judgment** (persist the document) but **failed the tool
mechanics** — it did not understand dsh's `write` tool `sandbox_permissions`
contract, passed an invalid value, and looped 3×.

- `_child_env` = "write code to a spec" → **passed.**
- filing the doc = "operate an unfamiliar 50-tool harness API" → **failed on the
  contract, not the reasoning.**

**Implication:** the worker should not have tool-use at all. It emits the artifact
as text; a deterministic orchestrator step does the file IO. This is exactly what
`meta/loop.py` already does (parse worker response → the *driver* writes the file
→ run_checks) and what META-3 means by "Qwen worker on a very narrow tool policy."
`dsh --profile headless` handing the worker the full dsh toolset is the bug, on
the harness side.

---

## Priority order

The proposal's order is right:

1. **Translation / decomposition layer** (highest value)
2. **`dsh.headless` as a real execution consumer**
3. **Portability / PATH hygiene** (infrastructure, not the research question)

One amendment: item 1's first deliverable is **not** the decomposer — it is the
**verifier-authoring method**. A decomposition you cannot verify per-job is not
executable, only impressive.
