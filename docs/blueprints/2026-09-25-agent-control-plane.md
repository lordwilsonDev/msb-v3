# MSB-v3 Agent Control Plane Blueprint
### From observer to governed decision-and-execution system · 2026-09-25

> **Status:** PROPOSED — nothing in this document is built. Decisions D-1…D-7
> decided 2026-09-25 (see §13); no phase starts until its gate in §12 is
> signed off.
> **Source:** Wilson's direction of 2026-09-25 (the "convergence first, then the
> control tower" plan), grounded here against the repository at `a8cd6d8`.
> **Scope:** single-operator, local-first. One mission at a time until M1 (§11)
> has been measured.

---

## 1. Thesis

MSB-v3 today is a substantial **observer**: the cockpit (`/cockpit`) and the
Background windows answer *"what is happening?"* and are read-only by design.

The product Wilson described is a different class of system: a cockpit that
**commands an AI engineering workforce under MSB-v3 governance** and answers
*"what should happen next?"*.

The jump is not another dashboard panel, agent, or infrastructure pile. It is
one spine — the **Mission** — that every role reads from and writes to, and one
final authority — the **MSB-v3 gate** — that no model can talk its way past.

```text
MODEL      proposes
HARNESS    constrains execution
VERIFIER   evaluates evidence
MSB-v3     enforces deterministic policy
HUMAN      resolves decisions that cross the authority boundary
```

"Who controls the controller?" stays answered by that ladder. A verifier saying
PASS never merges anything; MSB-v3 decides GREEN / REVIEW / BLOCK, and a merge
that crosses the authority boundary still needs Wilson.

---

## 2. Stage 0 — converge before expanding

No productization work starts on top of an unmerged 29-commit branch.

| Step | State (2026-09-25) |
|---|---|
| Commit `memory_verify` idempotence | DONE `642a7f3` — same-state request returns `no_change: true`, not HTTP 500; the fabric state machine is unchanged |
| Commit calibration observation families | DONE `8c32221` — the old MAPE 282 is **superseded** (it measured a cross-scope comparison, not forecast error) |
| Full regression | 3,875 passed / 0 failed / 17 skipped on the uncommitted tree before those commits; re-run on the committed HEAD before merge |
| Calibration regression | run `/plei/calibrate?domain=project_duration` and `?domain=run_duration`; record the two reports as the new baseline |
| Production gate | `make production-gate` on the final HEAD; evidence manifest kept |
| Review the branch diff (`main..feat/speech-stream-gate`) | open — Wilson |
| Merge to `main` | open — Wilson |

Exit: `main` contains the branch, the production gate passed on that commit, and
the calibration baseline is recorded. Only then does Phase 1 begin.

---

## 3. What already exists (reuse map)

The plan must converge existing satellites, not add new ones. Every row below is
a real module; the "gap" column is what this blueprint adds.

| Concept | Existing piece | Gap |
|---|---|---|
| Mission spine | `triumvirate/mission_anchor.py` (goal lock + scope hash); `factory/models.py` (`Issue → Plan → BuildResult → TestEvidence → Review → Verification → FactoryRun`) | no versioned Mission object linking blueprint → plan → task graph → results |
| Task contract | `docs/task-contract-v1.md` (machine-readable node: permissions, verification predicates, side effects, rollback, bounded decomposition); `tasks/models.py` `UnifiedTask` | contract is not yet the unit a worker harness receives |
| Worker identity | `agent/identity.py` `AgentRegistry` (fingerprinted, capability-scoped, revocable) | no harness binding identity ↔ workspace ↔ budget |
| Worker providers | `agent/providers.py`: `LocalAgentProvider`, `CliAgentProvider` (Claude Code / Codex / OpenCode), `PaseoAgentProvider`, `AnthropicAgentProvider`, `DshAgentProvider` (DeepSeek Harness), plugin entry points | no Hermes or FreeBuff provider; no role layer above providers |
| Isolated workspace | `factory/builders.py` `create_worktree` (git-free, secret-free copy) | not per-worker, not parallel-aware |
| Capability registry | `plei/engineering/capability_graph.py` (capabilities, skills, roles, providers per stage) | not a governed registry with trust, cost, credentials, allowed agents |
| Router | `plei/decisions/provider_selection.py` (profiles: capabilities, risk tier, availability, success rate, latency) | no cost policy, no permission check, hard-coded profile list |
| Independent review | `factory/reviewer.py`, `factory/verifier.py` | verifier sees code, not the blueprint's acceptance criteria |
| Deterministic gate | `scripts/production_gate.py` (explicit gate catalog, no silent skips, evidence manifest, waivers) | no per-mission gate with GREEN / REVIEW / BLOCK |
| Governance brakes | killswitch, budgets, operator auth (`/governance/status`) | reused as-is |
| Evidence | audit chain + ledger v2 (`src/msb_ledger/`), evidence stream | reused as-is |
| Research lane board | `ai-workspace/job-board/` (folder = status; FreeBuff executor, Hermes researcher) | the manual precedent for §6; replaced by the Mission, not duplicated |

---

## 4. The Mission object

One canonical, versioned object. Each stage produces an **immutable version**
that names its parent by content hash, so any artifact can be traced to the
exact blueprint it was built against.

```text
MISSION v1 ─► BLUEPRINT v1 ─► PLAN v1 ─► TASK GRAPH v1 ─► BUILD RESULTS ─► VERIFICATION ─► GATE
                  │                                                              │
                  └──────── a change to the blueprint = BLUEPRINT v2 ────────────┘
                            (every downstream artifact is then STALE, not silently reused)
```

```text
Mission
├── mission_id, created_at, created_by, state
├── objective            one sentence, operator-authored
├── constraints          budget, deadline, forbidden paths, cost policy (§7)
├── research_requirements  what must be sourced before a blueprint is accepted
├── evidence_requirements  what the gate will demand (§9)
├── blueprint_ref        {version, sha256}
├── plan_ref             {version, sha256, blueprint_sha256}
├── tasks[]              task-contract-v1 nodes, each with blueprint_version
├── workers[]            harness_id per task
├── verification[]       per-task + mission-level reports
├── decisions[]          human decisions, with who/when/why
├── costs                tokens, $ and wall clock, per role and per worker
└── artifacts[]          path + sha256, append-only
```

**State machine** (frozen before code, same discipline as the memory-verify
matrix):

```text
DRAFT → RESEARCHING → BLUEPRINT_REVIEW → PLANNING → PLAN_REVIEW
      → BUILDING → VERIFYING → GATED{GREEN|REVIEW|BLOCK} → MERGED | ABANDONED
any state → BLOCKED_ON_HUMAN → (back to the state it came from)
```

Storage: SQLite + JSONL artifacts under `data/missions/<mission_id>/`,
every transition written to the audit chain. Human-gated transitions:
`BLUEPRINT_REVIEW → PLANNING`, `PLAN_REVIEW → BUILDING`, `GATED → MERGED`.

---

## 5. Roles

Roles are **contracts**, not vendors. Each role names the provider it uses
today, but the router (§7) may bind any provider that satisfies the contract.

### 5.1 PM / Research Director — `CLAUDE-PM`
Understand the objective → research → search the capability registry → collect
citations → identify requirements and unknowns → write acceptance criteria →
produce the blueprint.

Outputs (artifacts, not chat):

```text
blueprint.json   blueprint.md   sources.json   citations.json   acceptance.json
```

Every acceptance criterion must be machine-checkable or explicitly marked
`human_judgement`, so the verifier and the gate have something to test.

### 5.2 Planner — `PLANNER`
Input: blueprint + repo state (codegraph) + architecture + constraints + existing
tests. Output `implementation-plan.json`: epics, tasks, dependencies,
parallelization, files, interfaces, test plan, rollback plan, risk, unknowns.
The planner answers *"how do we build what the blueprint specifies?"* — never
*"should the requirements be trusted?"*.

**Correction to the source text:** the remote DeepSeek API seam was retired on
2026-09-09 (decision D1, `docs/blueprints/2026-09-09-production-hardening.md`).
What remains is the **DeepSeek Harness** (`DshAgentProvider`, kind `dsh`), a
bounded subprocess currently marked `available=False` in the provider profiles.
The Planner role is therefore provider-agnostic: DSH is one candidate, the local
model or Claude are others. A DeepSeek key is an optional accelerator, never a
dependency (§13, D-2).

### 5.3 Workers — Hermes, FreeBuff, Codex, others
Workers build; they are not the brains. Every dispatch carries:

```text
mission_id  task_id  blueprint_version  task_spec (task-contract-v1)
allowed_tools  allowed_files  required_tests  done_criteria
```

- **Codex:** reachable today through `CliAgentProvider` (`cli.codex`).
- **Hermes, FreeBuff:** no provider exists yet — each needs an `AgentProvider`
  adapter (the plugin entry-point mechanism already supports drop-in workers).

### 5.4 Verifier — `CLAUDE-VERIFY`
Receives the original blueprint, plan, task contract, git diff, test results,
artifacts, runtime evidence and dependency changes, and asks **"did they build
what the blueprint required?"**, criterion by criterion against
`acceptance.json` — not "does the code look good?".

**Context separation is mandatory:** the PM and the verifier are separate
harness instances with separate identities and no shared conversation. The
verifier never sees the PM's reasoning, only its artifacts.

---

## 6. Harnesses

A harness is what makes a role safe to run. It owns:

```text
identity (AgentRegistry fingerprint)   workspace (own worktree)
tools + credentials (least privilege)  permissions (task-contract §6)
timeouts                                token / $ budget
task boundary (allowed_files)           test commands
artifact collection (sha256 on exit)
```

Invariants:

- **H1** A harness cannot assume another harness's identity (Hermes cannot
  become FreeBuff; FreeBuff cannot become Claude).
- **H2** A worker cannot write outside `allowed_files`; a violation is a BLOCK,
  not a warning.
- **H3** No harness holds merge authority. Codex cannot silently become the
  authority; neither can Claude.
- **H4** Budget exhaustion stops the harness and records the partial state; it
  never silently degrades to a cheaper model.
- **H5** Parallel tasks get parallel worktrees only when the plan marks them
  file-disjoint.

---

## 7. Capability registry, router and cost policy

**Registry.** Connectors and marketplace entries become governed capabilities
(browser, github, web-search, filesystem, database, deployment, automation,
document-generation, …). Each entry:

```text
provider  permissions  cost  trust_level  availability
required_credentials  allowed_agents  evidence_requirements
```

Claude may *discover* capabilities; MSB-v3 decides whether a given worker may
*invoke* one. Built by extending `capability_graph.py`, not beside it.

**Router.** `TASK → capability match → ROUTER → {Hermes | FreeBuff | Codex | DSH | local | other}`,
deciding on capability, cost, availability, permissions, model/tool
requirements, complexity and local/remote constraints. No `TASK TYPE X =
ALWAYS HERMES` rules. Built by extending `provider_selection.py` (replacing the
hard-coded profile list with the registry).

**Cost policy** — a mission constraint, not an architecture choice:

```text
FREE_FIRST   LOCAL_FIRST   QUALITY_FIRST   LATENCY_FIRST   OPERATOR_OVERRIDE
```

`FREE_FIRST`: can a free provider perform it? yes → free route; no → escalate
to paid/local/frontier, and record why.

---

## 8. Verification ladder

Different failure paths on purpose — each rung catches what the one before can
miss.

```text
Builder (Hermes / FreeBuff / Codex)   self-test: the task's required_tests
Planner                               plan ↔ code consistency + test review
Codex (optional)                      independent specialist check
CLAUDE-VERIFY                         acceptance verification against the blueprint
MSB-v3 gate                           deterministic checks — no model judgement
```

A builder's self-test result is **evidence the gate re-runs**, not evidence the
gate trusts.

---

## 9. The MSB-v3 mission gate

Extends `scripts/production_gate.py` (same no-silent-skips and waiver rules).

| Check | Source |
|---|---|
| required tests ran and passed | re-run by the gate, not read from the worker |
| required evidence present | `evidence_requirements` in the Mission |
| policy | MoIE risk templates, governance budgets, killswitch |
| authorization | actor identity per artifact (AgentRegistry) |
| artifact integrity | sha256 of every artifact vs the Mission record |
| version compatibility | every task built against the current `blueprint_version` |
| forbidden changes | diff ∩ forbidden paths = ∅; diff ⊆ union of `allowed_files` |
| security gates | secret scan, dependency diff, ruff/mypy |
| claim consistency | `scripts/verify_claims.py` + verifier report vs gate results |

Verdict: **GREEN** (merge may proceed with operator approval) · **REVIEW**
(a required check is UNKNOWN or waived — human decides) · **BLOCK** (a required
check failed). UNKNOWN never becomes GREEN.

---

## 10. Background decision loop and cockpit

**Loop** (runs only inside an active mission; proposes, does not self-authorize):

```text
OBSERVE → UNDERSTAND → FIND GAP → PRIORITIZE → CREATE TASK
       → DISPATCH WORKER → TEST → VERIFY → UPDATE MISSION → OBSERVE
```

PLEI (forecasting, calibration), the wake loop, the Background windows and the
task board converge here instead of remaining separate satellites. Task
creation above the mission's budget or outside its scope goes to
`BLOCKED_ON_HUMAN`.

**Cockpit reshape** — the existing panels stay as drill-downs; the top level
becomes the mission:

```text
┌─────────────────────────────────────────────────────┐
│ MISSION   Build: <objective>          State: BUILDING│
├─────────────────────────────────────────────────────┤
│ PM / RESEARCH      PLANNER          VERIFIER         │
│ Claude ● active    DSH ● planning   Claude ○ waiting │
├─────────────────────────────────────────────────────┤
│ WORKERS   Hermes T-019 BUILDING · FreeBuff T-020 …   │
├─────────────────────────────────────────────────────┤
│ BACKGROUND  4 active · 2 blocked · 1 human decision  │
├─────────────────────────────────────────────────────┤
│ EVIDENCE  Blueprint · Plan · Tests · Verify · Gate   │
└─────────────────────────────────────────────────────┘
```

Every value carries its source and freshness timestamp; stale or unavailable
reads render as UNKNOWN, never green (same rule as the cockpit build plan).
Consequential actions (dispatch, approve, merge) require operator auth.

**MEMORY panel — decision proposed.** Replace the single "Memory" card with four
labelled layers so no number mixes them:

| Layer | Source |
|---|---|
| Runtime memory | in-process evolution log (what the panel shows today; resets on restart) |
| Persistent memory | memory fabric (`memory_fabric`) |
| Evidence memory | ledger / audit chain |
| Research memory | research artifacts (`ai-workspace/`, vault research notes) |

---

## 11. Milestone M1 — one real mission, measured

The product thesis is tested before any further infrastructure: one real
project run through the whole chain, visible from the cockpit.

```text
Claude PM → blueprint → planner → Hermes + FreeBuff build → self-tests
→ planner review → Claude verify → MSB-v3 gate → Wilson merges
```

Stated before running (research-lane rule: failure conditions first):

| Measure | Recorded |
|---|---|
| Wall clock per stage, tokens and $ per role | Mission `costs` |
| Human interventions (count + reason) | Mission `decisions` |
| Defects each rung caught that the rung before missed | verification reports |
| Defects that reached the gate / escaped the gate | gate report + post-merge |
| Blueprint criteria met / unmet / unverifiable | verifier report |

**M1 fails** if: the gate issues GREEN on a change the verifier or Wilson later
finds violates `acceptance.json`; or any harness writes outside its
`allowed_files` without a BLOCK; or the chain needs more human interventions
than doing the task by hand would have. A failed M1 is a valid result and is
recorded, not re-run until it passes.

---

## 12. Phases

Each phase is a separate spec → plan → build cycle with its own tests; each
ends at a gate Wilson signs.

| # | Phase | Acceptance (gate) |
|---|---|---|
| 0 | Converge (§2) | branch merged; production gate passed on the merged commit; calibration baseline recorded |
| 1 | Mission schema + state machine + store | transition matrix frozen and property-tested; every transition on the audit chain; versions content-hashed; stale-downstream detection tested |
| 2 | Harness registry + worker harness base | H1–H5 each pinned by a test; per-worker worktree; budget stop records partial state |
| 3 | Capability registry + router + cost policy | registry drives `provider_selection`; no hard-coded profiles; each cost policy has a routing test; permission denial is a BLOCK |
| 4 | PM harness + blueprint artifact | the five artifacts produced and schema-valid; every acceptance criterion machine-checkable or tagged `human_judgement` |
| 5 | Planner harness | `implementation-plan.json` schema-valid; every task references the blueprint version and satisfies task-contract-v1 |
| 6 | Hermes + FreeBuff providers; Codex via `cli.codex` | each passes the provider conformance tests; self-test results captured as artifacts |
| 7 | Verifier harness | isolated context proven (no shared state with PM); report per acceptance criterion |
| 8 | Mission gate | every §9 check implemented or listed NOT_IMPLEMENTED (never silently skipped); GREEN / REVIEW / BLOCK tested |
| 9 | Cockpit mission view + MEMORY layers | read paths first; write actions behind operator auth; freshness on every value |
| 10 | M1 run (§11) | measured report written; Wilson rules on the thesis |
| 11 | Background decision loop | only after M1: proposes tasks inside a mission; nothing self-authorized |

Phases 1–3 are pure MSB-v3 code and can be built and tested without any
external worker. Phase 6 is the first that needs Hermes/FreeBuff integration.

---

## 13. Open decisions (Wilson)

| ID | Decision | Options |
|---|---|---|
| D-1 | **Lane conflict.** The research lane's working rule is "do not expand MSB-v3 during this phase" (`ai-workspace/RESEARCH-STATUS.md`). This blueprint is an expansion. | **Decided 2026-09-25: (c) run both, with the control plane in a separate worktree** — research lane stays alive; control-plane work happens in an isolated worktree so neither lane blocks the other |
| D-2 | Planner provider | **Decided 2026-09-25: router-chosen per mission** — any provider that satisfies the contract (Claude, DSH if available, local Ornith); chosen per-task by capability/cost, not hard-wired |
| D-3 | **Hermes and FreeBuff role change.** The job board today forbids FreeBuff to commit or touch files outside `target_files`, and forbids Hermes to edit source. As builders, both write code. | **Decided 2026-09-25: keep the no-commit rule (workers write in worktrees; only the gate + Wilson merge)** — H3 holds: no harness has merge authority |
| D-4 | M1 project | **Decided 2026-09-25: bounded todo/board feature** — one real, small, bounded todo/board slice end-to-end through the full chain |
| D-5 | MEMORY panel | **Decided 2026-09-25: four layers as in §10** — Runtime / Persistent / Evidence / Research, each labelled so no number mixes layers |
| D-6 | Cockpit build plan (104 gates) | **Decided 2026-09-25: defer 104 gates behind M1** — finish the Mission spine first, resume the physical-gate matrix after M1 |
| D-7 | Branch merge (§2) | **Decided 2026-09-25: merge to `main` now — converge before expanding** — Stage 0 exit: branch merges to `main` after the re-run, calibration baseline and production gate |

---

## 14. Not doing

- Building all 104 cockpit physical gates before M1 — valuable, but an unbounded
  pull; its matrix already separates code evidence from physical evidence, and it
  can resume after M1.
- A new orchestrator beside the factory, task contract, agent registry and
  provider selection — this blueprint extends those.
- Hard-coded vendor-to-task routing.
- Any path where a model's PASS merges code.
- Starting Phase 1 before Stage 0 exits.
