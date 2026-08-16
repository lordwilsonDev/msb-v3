# M1 — Core Loop: the Governed Agent Handle Loop

**Status:** COMPLETE (evidence gate) · **Dated:** 2026-08-16 · **Owner:** Wilson

## 1. Canonical workflow — selected

**`handle(request)` → governed agent loop** (`POST /agent/handle`, `src/msb_v3/agent/handle.py`).

Why this one: it is the only existing path that exercises **all six** M1
requirements in one loop — model reasoning (intent + plan), tool/data
interaction (vault search), governance **before** action (ActionGate +
taint + grant + kill switch), evidence recording (spine + trace + audit
chain), verification (grounded, no LLM judge), and a recoverable failure
mode (replay engine + defined terminal states). It is the proof-burden
carrier for v3.

### Deliberately rejected alternative

**Daily research + Telegram digest** (`/research/assistant/run` + the live
n8n 9:00 job). Rejected because: it is a one-shot pipeline with no
consequential tool actions to govern (no ActionGate path), no taint model,
and a weaker failure surface — it cannot demonstrate "refuses an unsafe
action." It remains a real recurring workflow (M6 operating data), but it
does not carry the governance proof burden.

## 2. State machine (as implemented in `handle.py`)

```
        ┌──────────────────────────────────────────────────────────────┐
        │ REQUEST                                                       │
        │  empty → ERROR (no run_id)                                    │
        ▼                                                               │
   [TASK_CREATED] ── create unified task (run_id = dbb-{ts}-{h})        │
        ▼                                                               │
   [INTENT_INTERPRETED] ── LLM extract: goals / permissions / privacy   │
        ▼                                                               │
   [PLAN_CREATED] ── plan() → Task DAG (goal, tasks, dependencies)      │
        ▼                                                               │
   [AGENT_STARTED / EXECUTING]                                          │
        │                                                               │
        ▼  per task (DAG order, parent-before-child):                   │
   [TOOL_REQUESTED] ── SafeProvider.gate(capability,                    │
        │                tainted_inputs, approved, granted)             │
        │   ├─ BLOCK  → GateBlocked → TASK_FAILED → DENIED (no action)  │
        │   ├─ REVIEW → GateReview  → TASK_FAILED → DENIED (no action)  │
        │   └─ SAFE   → [TOOL_EXECUTED] ── asyncio.wait_for(timeout_s)  │
        │                  ├─ ok   → [POLICY_CHECKED] → grounded verify │
        │                  └─ fail → retry:N or fail → dependents SKIPPED│
        ▼                                                               │
   [VERIFYING] ── per-task grounded verification (no LLM judge)         │
        ▼                                                               │
   [EVIDENCE_RECORDED] ── spine decision→execution→verification,        │
        │                  trace:run_start/plan/execution/outcome,      │
        │                  deterministic_hash (content-addressed)       │
        ▼                                                               │
   [TASK_COMPLETED | TASK_FAILED] ── HandleResult verdict               │
        ▼                                                               │
   REPORT (PASS/FAIL)  |  RECOVER: ReplayEngine.replay_task(run_id)     │
        └──────────────────────────────────────────────────────────────┘
```

**Terminal states** (HandleResult.verdict): `PASS` (all tasks verified) ·
`FAIL` (a task failed verification) · `ERROR` (exception / empty request /
unknown or revoked agent / no provider) · `REVIEW` (tainted or high-tier
action refused pending approval) · `BLOCKED` (kill switch / not granted /
very-high-risk tier).

**Lifecycle states** (`tasks/lifecycle.py`): CREATED → INTENT_INTERPRETED
→ PLANNED → EXECUTING → VERIFYING → COMPLETED / FAILED / DENIED.

## 3. Boundary contracts

### Input (the request)
- Type: `str` (the task in natural language). Empty → `ERROR`.
- The operator may pass `approve=True` to pre-authorize the intent's
  declared permissions (tainted writes then execute); default `False` —
  tainted writes require review.

### Classify → Intent (`agent/intent.py`)
- LLM-first: `goals`, `constraints`, `permissions` (capability hints),
  `privacy` (default True), `domain`.
- Fail-closed fallback: unparseable model output degrades to a minimal
  Intent; `write_file` is added when the request lexically asks to write.
- Permissions are the *request*, not the grant — the gate decides.

### Plan → Task DAG (`agent/planner.py`, `agent/dag.py`)
- Output: `TaskGraph {goal, source, tasks[{task_id, capability, tool,
  inputs, depends_on, timeout_s, retry_policy}]}`.
- Invariant: no task runs before its parents succeed; failed parents skip
  dependents (recorded as `skipped`, not silently dropped).

### Authorize → ActionGate (`agent/safety.py`)
- Two axes: **severity tier** (`read_vault=1 … permissions=4`; REVIEW at
  tier ≥3, BLOCK at tier ≥4) and **provenance taint** (untrusted tool
  output driving a write escalates to REVIEW unless pre-approved).
- **Kill switch** (global + scoped tool/agent/tenant): armed → BLOCK.
- **Grant whitelist** (agent identity §17): capability ∉ grant → BLOCK.
- Verdicts: `SAFE` / `REVIEW` / `BLOCK`; every refusal is appended to the
  audit chain. Caller must honor the verdict (SafeProvider raises so the
  executor's failure path handles it uniformly).

### Execute → Executor (`agent/executor.py`)
- Per-task `timeout_s` via `asyncio.wait_for` (timeout → task error).
- `retry_policy="retry:N"` retries transient tool failures up to N.
- Tool outputs flow through `RuntimeStore`; taint propagates with data
  (a dead taint at intermediate nodes would let injected instructions
  drive an unapproved write).

### Observe → lifecycle + observation sink
- Every tool event (`TOOL_REQUESTED/TOOL_EXECUTED/POLICY_CHECKED/
  MUTATION_COMMITTED`) is emitted on the unified task + mirrored to the
  audit chain (best-effort — an outage degrades provenance, never the run).

### Verify → grounded, deterministic (`agent/trace.py`)
- Per-task `verify_task(task, output)` → `{ok, kind: grounded, check,
  detail}`. **No LLM judge anywhere in the verification path.**
- `deterministic_hash` = SHA-256 over {request, intent, graph_source,
  tasks, execution, verdict} — excludes timestamps/latency, so same
  evidence ⇒ same hash, and it can be recomputed to prove non-tampering.

### Record → evidence spine + audit chain
- Spine: `decision → execution → verification` vertebrae linked by
  `parent_decision_id`, all keyed `task_id == run_id`, `policy_version=
  "handle-gate-v1"`, `policy_result ∈ {ALLOW, REVIEW, DENY}`.
- Chain: `trace:run_start/plan/execution/outcome` under component
  `agentic`. Best-effort by design: a spine/chain outage must never break
  the run (documented I7 invariant).

### Report / Recover
- Report: `HandleResult {ok, run_id, verdict, deterministic_hash, trace,
  error}`.
- Recover: `ReplayEngine.replay_task(run_id)` reconstructs derived state
  from events and reports `consistent` / `legal`; recoverable states
  resume, unknown states halt (never guess).

## 4. Golden fixtures (committed)

`docs/blueprints/convergence-to-12/fixtures/handle-loop/`

| File | Purpose |
|---|---|
| `request.json` | Canonical requests: happy-path (PASS), tainted-write (REVIEW), kill-switch (BLOCKED) + expected terminal states |
| `expected-evidence-shape.json` | The schema every run must satisfy (fields, enums, hash fields, vertebrae links) |
| `recorded-trace.example.json` | A real live PASS run (local `qwen2.5-coder:0.5b`): 2 tasks, both grounded-PASS (`search_returned_hits`, `synthesis_nonempty`), hash `fb0b15ed6c48aedb`. Labeled example — live runs are not byte-golden; the invariants are the shape + replay property |

## 5. Baseline observability (exit criterion)

The **run identifier** is `run_id = dbb-{ts}-{hash}`. It links: the unified
task (lifecycle events), the evidence spine (`task_id == run_id`), the
trace, and the audit chain (`trace:*` events carry `run_id`). One id answers
"what happened, why, with which model, under which policy, with what result"
across all stores — verified live in the recorded fixture.

## 6. What M2 builds on

M2 (Governance in the Loop) starts from an already-gated path: the remaining
proof is a dedicated **bypass regression suite** (alternate callers / direct
tool invocation must not escape the gate), governance **metrics** that
distinguish allowed/denied/indeterminate/failed, and a documented
denial→recovery flow exercised as tests, not just code paths.
