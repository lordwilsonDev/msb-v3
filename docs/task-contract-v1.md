# Task Contract — v1 Spec

**Status:** spec-for-review (the workflow-mode extension of the conversation envelope)
**Date:** 2026-08-10
**Depends on:** [`conversation-envelope-v1.md`](./conversation-envelope-v1.md) (the
interface it extends) + [`conversation-ledger-producer-v1.md`](./conversation-ledger-producer-v1.md)
(the log hop it terminates in) + [`conversation-e2e-harness-v1.md`](./conversation-e2e-harness-v1.md)
(the probe that asserts it)
**Schema version:** `1.0`

> The model doesn't get the final say. A task contract is a machine-readable
> job specification: the executor receives the contract, not a prompt; the
> verifier proves the `expected_output`, not the executor's word; the ledger
> records the verdict as evidence, not as ceremony. Exit 0 ≠ task done.

---

## 1. Purpose

The conversation envelope's `chat` mode is one bounded exchange. `workflow`
mode is the same envelope driving an explicit task graph — and a graph of
unbounded prompts is not an execution substrate, it's a wish list. The Task
Contract makes every dag node a **machine-readable job specification** with
four things a prompt cannot carry:

1. **a verifiable `expected_output`** — what "done" means, in predicates
2. **a declared permission envelope** — what tools and data the task may touch
3. **a declared rollback plan** — how the world is restored if the task fails
4. **bounded decomposition** — caps that make graph explosion impossible by construction

It is the workflow-mode extension of the envelope: `workflow.dag` entries gain
contract fields, and each contract execution produces the same kind of §8
ledger evidence the conversation producer already emits — two producers, one
evidence schema, one claim registry.

**Grounded in existing code, not invented:**
- the envelope's `WorkflowSpec` (`goal`, `dag: [{skill, args}]`, `step_tracker`)
- `guardrails/fold.py` `StepTracker` (`required_steps`, `is_satisfied`, `pending`)
- ralph-loop `Constraints` (budget caps) + `Status` (`READY | RUNNING | COMPLETED | FAILED | OPEN`)
- the replay consumer's `claim:ok:task:<task_id>` ("task completes without failure") — the availability claim this contract shares, unchanged
- skill-orchestration-os's DAG planner + replan-on-failure (the reality feedback loop)

---

## 2. The contract (workflow.dag node extension)

Each entry in `workflow.dag` may carry the full contract. Minimal form
`{skill, args}` (today's shape) stays valid — it is an *unverified* contract
(`expected_output` absent ⇒ the task can only ever be `INCONCLUSIVE`/`UNVERIFIED`,
never VERIFIED). The contract is the dag node, not a parallel object.

```json
{
  "task_id": "memory.003",
  "objective": "Implement semantic retrieval",
  "skill": "retrieval",
  "args": { "collection": "vault" },

  "inputs": [ "memory.002:out" ],

  "allowed_tools": [ "local_llm", "filesystem" ],
  "allowed_data": [ "tenant:default" ],

  "constraints": {
    "budget_cap_usd": 0.01,
    "max_steps": 8,
    "stall_threshold": 3
  },

  "preconditions": [ "memory.002:VERIFIED" ],

  "expected_output": {
    "schema": { "type": "object", "properties": { "module": { "type": "string" } } },
    "predicates": [
      { "kind": "file_exists", "path": "out/retrieval.py" },
      { "kind": "file_contains", "path": "out/retrieval.py", "text": "def search" }
    ]
  },
  "verification": "on_submit",

  "side_effects": [ { "kind": "file_write", "path": "out/**" } ],
  "rollback": { "kind": "git_revert", "scope": "out/" },

  "confidence": 0.91,
  "parent": null,
  "status": "READY"
}
```

**Field rules (fail fast, 422 on violation):**

| Field | Rule |
|---|---|
| `task_id` | REQUIRED; non-empty; unique within the dag |
| `objective` | REQUIRED; non-empty human/machine-readable statement |
| `skill` | REQUIRED; must resolve to a registered domain skill |
| `args` | validated against the skill's declared arg schema (the skill router's contract) |
| `inputs` | must reference `task_id`s earlier in the dag (no forward refs); absent = leaf |
| `allowed_tools` | subset of the executor's registered tool set; absent = `[]` (no tools — deterministic-function tasks only) |
| `allowed_data` | tenant-scoped; absent = the request's `tenant_id` |
| `constraints` | mirrors ralph-loop `Constraints`; caps are ENFORCED, not advisory |
| `preconditions` | `task_id:VERIFIED` pairs; the task is `READY` only when all are satisfied |
| `expected_output` | `schema` + `predicates`; see §4. **Absent ⇒ the task can never reach VERIFIED.** |
| `verification` | `on_submit` (v1) / `external` (v1.1, a separate verifier process) |
| `side_effects` | declared effects; a task that touches undeclared paths violates its own contract |
| `rollback` | one restore plan; see §5 |
| `confidence` | planner's estimate — **evidence, not truth**; never a verdict input |
| `parent` | optional task_id for subtasks; nesting depth capped (see §7) |
| `status` | `READY | RUNNING | SUBMITTED | VERIFIED | FAILED | ROLLED_BACK` — see §3 |

Unknown contract fields are rejected (`extra=forbid`, matching the envelope's
fail-fast doctrine): a forward-incompatible contract must not be half-served.

**The discriminator (pinned): `task_id` present ⇒ full contract validation;
absent ⇒ the legacy `{skill, args}` unverified form.** `WorkflowSpec.dag` is
`list[dict[str, Any]]` with no pydantic discrimination, so the validator's
rule must be explicit — never validate on a field that the legacy form could
also carry.

---

## 3. Status machine (what "done" means, made explicit)

The ralph-loop `Status` machine (`READY → RUNNING → COMPLETED/FAILED`) proves
*execution happened*. The contract adds the step the session's whole doctrine
exists for: *proof that the output holds*.

```
READY ──(preconditions met)──► RUNNING ──(executed)──► SUBMITTED ──(verification)──► VERIFIED
   │                              │                         │                            │
   │                              │                         └──(predicates fail)──► FAILED
   │                              └──(crash / budget hit)──► FAILED                   │
   └──(precondition unmet)──────────────────────────────────────────────────► (stays READY, never RUNNING)
```

| State | Meaning |
|---|---|
| `READY` | contract valid, preconditions unmet or met; the only state a router may select |
| `RUNNING` | executing; budget ticking against `constraints` |
| `SUBMITTED` | side effects done, exit 0 — **NOT done**; awaiting predicate evaluation |
| `VERIFIED` | all `expected_output.predicates` pass — the only terminal success |
| `FAILED` | predicates failed, crash, or budget exhausted; distinct from VERIFIED regardless of exit code |
| `ROLLED_BACK` | FAILED + rollback executed; restoration independently confirmed |

**The contract's central invariant: `exit 0 ≠ VERIFIED`.** A task that exits 0
with failing predicates is `FAILED`, and the ledger must record it as such —
this is the exact "green means green" doctrine applied at task granularity.
A task with no `expected_output` can reach at most `SUBMITTED` and its claim
can never be VERIFIED (the producer's no-evidence rule, applied).

---

## 4. Verification predicates (the "task complete" definition)

Predicates are **deterministic, stdlib-only, zero-spend** checks over the
task's declared output — never the model's judgment, never network. v1 kinds:

| Kind | Args | Passes when |
|---|---|---|
| `exit_code` | `code: 0` | the executor exited with that code (weakest — never sufficient alone) |
| `file_exists` | `path` | the declared output file exists |
| `file_contains` | `path`, `text` | the file contains the exact substring |
| `file_not_contains` | `path`, `text` | the file does not contain the substring |
| `output_equals` | `expected` | the task's returned output equals the value (exact, canonical_json) |
| `artifact_hash` | `path`, `sha256` | the file's sha256 matches (content-addressed, tamper-evident) |

**Constraints on predicates:**
- No `eval`, no arbitrary code in the contract. A contract whose verification
  is `{"kind": "custom", "fn": "..."}` is **rejected at validation** — it is a
  code-injection surface dressed as a contract. New predicate kinds are added
  to the verifier's registry, reviewed, and tested — never to the contract.
- **VERIFIED requires ≥ 1 predicate stronger than `exit_code`** — a contract
  whose `expected_output` is only `[{kind: "exit_code", code: 0}]` is rejected
  at validation (it would VERIFIED on exit code alone, the exact green-means-
  green hole this contract exists to close). `exit_code` may corroborate, never
  stand alone.
- Predicate paths are resolved **inside the task's declared output scope**
  (`side_effects`/`args` paths); path traversal outside scope fails the
  predicate and flags the contract.

**`verification: on_submit` (v1):** the executor runs the predicates at submit
time. This is acceptable because predicates are deterministic code — the model
judges nothing; it merely invokes the checker. **`external` (v1.1):** a
separate verifier step runs the predicates against the submitted artifact set,
so a compromised executor cannot attest its own output. The polarity of the
ledger artifact is bound by the **verifier's** run in both versions.

---

## 5. Side effects & rollback (declared, not discovered)

Every contract declares what it may touch and how it is undone:

```json
"side_effects": [ { "kind": "file_write", "path": "out/**" } ],
"rollback": { "kind": "git_revert", "scope": "out/" }
```

- **Side-effect scope is a guardrail, not documentation:** writing outside the
  declared paths is a contract violation (logged, recorded as FAILED) — the
  permission model of §6 applied to the filesystem.
- **Rollback kinds, v1:** `git_revert` (restore `scope` to pre-task HEAD),
  `file_delete` (remove created files in `scope`). `state_restore` and
  container/sandbox restore land in v1.1.
- **Confirmation is predicate-based (the same §4 machinery, applied to the
  restored scope):** after `file_delete`, confirm `file_not_exists`; after
  `git_revert`, confirm the scope's HEAD matches the pre-task commit and the
  scope is clean. Rollback confirmation is not aspirational — it is the
  verifier, run over the restored state.
- **Failed rollback is an incident, not a silent success** (the EAAE doctrine,
  blueprint §14): `ROLLED_BACK` requires the confirmation predicates to pass;
  a rollback whose confirmation fails leaves the task `FAILED` and emits a
  ledger event, never a false `ROLLED_BACK`.

---

## 6. Permissions (the security engineer's layer, made concrete)

`allowed_tools` + `allowed_data` are the **enforcement envelope** the tool
broker checks on every tool call, per task:

- `allowed_tools` — the executor may only invoke tools in this list. A call to
  a tool outside the list is denied and recorded (mirrors ralph-loop's
  `allowed_tools=["local_llm","filesystem"]` governance gate, now at task
  granularity).
- `allowed_data` — tenant-scoped retrieval; a task may not read outside its
  declared tenant (the RetrievalRouter already takes `tenant_id` — the
  contract pins it per task).
- Defaults fail closed: absent `allowed_tools` ⇒ no tools (deterministic
  function only); absent `allowed_data` ⇒ the request's `tenant_id` and
  nothing else.

The contract is the permission envelope: a task cannot access more than its
contract declares, regardless of what the model attempts.

---

## 7. Bounded decomposition (the graph-explosion governor)

The essay's sharpest criticism: a goal that decomposes into 2,000-task
bureaucracy. The answer is **caps, enforced at validation — not at runtime, not
by convention**:

| Cap | Default | Enforced where |
|---|---|---|
| `max_dag_depth` | 3 (goal → objective → task) | contract validation — a deeper dag is rejected, not executed |
| `max_dag_nodes` | 50 | contract validation |
| `max_nodes_per_level` | 20 | contract validation |
| `budget_cap_usd` | 1.00 (inherits ralph-loop default) | executor — exceeded ⇒ FAILED |
| `max_steps` | 12 | executor — exceeded ⇒ FAILED |

A dag that violates a cap fails validation with the specific cap named — the
planner must adaptively decompose *within* the budget (replan, merge, or
escalate), never blow past it. This is the complexity governor from the
architecture doctrine, turned into a number that a test can assert.

**The complexity ladder (deterministic before new machinery):** a contract's
`skill`/`allowed_tools` choice is validated against the ladder — can a
deterministic function do it? can the graph? can retrieval? can an existing
skill? — before any new subsystem may be introduced. The ladder is a
validation gate, not a prompt convention.

---

## 8. Claims & the ledger (one registry, two producers)

Each contract execution emits ONE §8 artifact through a task producer that
mirrors the conversation producer exactly (same shape, same idempotency, same
fail-loud). Two claim families, mirroring the conversation pair:

| Claim | Formula | Question | Truthfulness |
|---|---|---|---|
| `claim:ok:task:<task_id>` | the task_id as-is (**identical to the replay consumer's derivation** — one availability claim across producers) | "Does this task complete without failure?" | Availability — a failed/rolled-back task is not OK |
| `claim:done:task:<hash>` | `sha256(canonical_json({task_id, expected_output, predicates}))[:12]` | "Is this task's declared output, as specified, actually produced?" | Epistemic — the expected_output holds or it doesn't |

**Polarity mapping (mirrors producer §4):**

| Contract outcome | Polarity | Claim | Evidence type |
|---|---|---|---|
| `VERIFIED` | `SUPPORTING` | `claim:done:task:<hash>` | `task_verified` |
| `SUBMITTED` (no expected_output) | `INCONCLUSIVE` | `claim:done:task:<hash>` | `task_submitted` |
| `FAILED` (predicates fail) | `CONTRADICTING` | `claim:done:task:<hash>` | `task_failed_verify` |
| `FAILED` (crash/budget) or `ROLLED_BACK` | `CONTRADICTING` | `claim:ok:task:<task_id>` | `task_failed` |
| any FAILED | `TASK_FAILED` event to the existing feedback stream | — | replay consumer picks it up, unchanged |

- **Verdict transitions:** the producer spec's §7 table applies verbatim —
  `SUPPORTING → VERIFIED`; previously-verified + `CONTRADICTING → REGRESSED`;
  never-supported + `CONTRADICTING → UNVERIFIED`; `INCONCLUSIVE` never
  elevates. Same governor, new evidence types.
- **The reality loop closes natively — with the event shape PINNED:** a failed
  contract writes a `TASK_FAILED` event to the feedback stream the replay
  consumer already ingests, but "zero changes" is only true with the exact
  shape the consumer parses. The emitted event MUST carry:
  `event: "TASK_FAILED", version: "1.0", task_id, goal, failed_step,
  failed_index, error, attempt, max_attempts, decision, dag, revised_dag, ts`
  (an event missing any parsed field is silently skipped, not ingested), and
  **`event.task_id == contract.task_id` verbatim** — no normalization — so
  `claim:ok:task:<task_id>` collides across producers and the existing REGRESS
  detection fires. The contract is the missing producer for the loop the
  conversation producer started.
- **Idempotent:** re-executing the same contract produces the same
  `claim:done:task:<hash>`; the cursor dedupes. Same-second distinct contracts
  never conflate (content-addressed, per producer §6).

---

## 9. Envelope integration (what the endpoint does)

`workflow` mode with a contract-carrying dag:

1. **Validate the dag** (fail fast, 422): contract fields, caps (§7), forward
   refs, predicate kinds (§4).
2. **Router selects** exactly one `READY` task whose preconditions are met
   (the existing dag/step_tracker machinery; contract `status` is the new
   readiness signal).
3. **Execute** under the contract's permission envelope and budget (tool
   broker enforces `allowed_tools`; RetrievalRouter scoped by `allowed_data`).
4. **Submit** → run predicates (`on_submit`, v1) → `VERIFIED` or `FAILED`.
5. **Log** → task producer emits the §8 artifact; the conversation producer's
   record stream gains a `task` record type.
6. **Replay** → TASK_FAILED events flow to the existing consumer; the offline
   replay eval gains task metrics (verify-rate, rollback-rate, cap-violation
   rate) with zero spend.

**Execution granularity (pinned): v1 executes exactly ONE contract per `/ask`.**
The router selects one `READY` task, executes it under its contract, verifies,
logs, and the response returns the **updated dag state** (per-task `status`
for every node) — the next `/ask` with the same `session_id` advances the
graph. This matches the envelope's one-exchange-per-call shape and the vision's
"task router selects exactly one ready task." Whole-dag-synchronous execution
and `deferred`/polling are v1.1 modes (envelope open decision 6). Without this
pin, an implementer guesses, and the E2E cannot assert the granularity.

The endpoint stays synchronous for contracts within budget. The E2E probe
asserts: every dag node has a contract; no VERIFIED without passing
predicates; a fixture `stub://task-fails-predicates` contract is FAILED despite
exit 0; and one `/ask` advances exactly one node.

---

## 10. CI / stub profile

- The stub executor deterministically resolves a contract: predicates run for
  real against fixture outputs; a fixture contract (`stub://task-fails`) exits
  0 but fails its predicates — the probe asserts it lands FAILED, not VERIFIED
  (the exit-0-≠-done invariant, machine-checked).
- The task producer is zero-spend by construction (like the conversation
  producer) and runs live in CI: the stub E2E executes contracts → artifacts
  land in a scratch ledger → replay ingests 0 new.
- `--self-test` covers: predicate kinds, cap enforcement, permission denial,
  rollback confirmation, polarity mapping, prohibited transitions.

---

## 11. Conformance checklist (what "done" means)

- [ ] Every workflow-mode dag node carries a validated contract (or is
      explicitly unverified and can never reach VERIFIED)
- [ ] `exit 0 ≠ VERIFIED` — predicates are the only path to VERIFIED; a
      fixture proves a failing-predicate task is FAILED despite exit 0
- [ ] Caps enforced at validation: depth, nodes, budget, steps — a violating
      dag is rejected, never executed
- [ ] `allowed_tools`/`allowed_data` enforced at the tool broker; undeclared
      side effects fail the task
- [ ] Rollback confirmed independently; failed rollback is an incident
- [ ] No `eval`/custom-code predicates — the predicate registry is the only
      verification surface
- [ ] `claim:done:task` + `claim:ok:task` artifacts in the §8 shape; replay
      consumer unchanged; offline replay eval reports verify/rollback/cap rates
- [ ] E2E probe runs the contract path under stub mode on every push, zero spend

---

## 12. Open decisions (resolve before implementation)

1. **Predicate registry ownership**: v1 ships the six kinds in §4. Who adds
   kinds — and what is the review gate (mutation-tested like everything else)?
   (Recommendation: a `predicates.py` with its own tests; a new kind is a
   code change that must pass the factory gate, never a contract change.)
2. **`external` verification**: v1 `on_submit` only, or ship the separate
   verifier in v1.0? (Recommendation: `on_submit` for v1 — deterministic
   predicates are already model-independent; the separate process is a
   hardening layer, not a correctness layer.)
3. **Rollback kinds**: `git_revert` + `file_delete` for v1, or also
   `state_restore`? (Recommendation: two kinds for v1; `state_restore` needs
   the snapshot machinery the blueprint's experiment isolation defines.
   Confirmation is predicate-based either way — see §5.)

6. **Graph state persistence**: one-contract-per-`/ask` needs dag state to
   survive across calls — `session_id` + the `step_tracker` already exist; is
   the dag state stored in the session memory store (`/assistant/memory`
   pattern) or a dedicated workflow store? (Recommendation: a dedicated
   `workflows/<session_id>.json` store — session memory is conversational
   context, and a workflow graph is structured state that the producer's
   record-is-source-of-truth pin needs to survive independently.)
4. **Cap values**: 3/50/20/1.00/12 — sane defaults, or config-driven per
   tenant? (Recommendation: defaults in code, env-overridable per tenant via
   `allowed_data` scope.)
5. **Task record type in the conversation stream**: new `record_type:
   "task"` field vs a sibling stream? (Recommendation: one stream, a
   `record_type` discriminator — the producer's `_validate_record` keys off
   it, keeping one cursor, one replay story.)
