# ARCHITECTURE — as implemented @ 7cb2f7f  (R0 candidate)

Method: re-verified the JOB-011 map (written @ 89a367f) against current source. Labels: **FACT** = read in code at the cited line; **INFERENCE** = reasoned from code, not executed; **UNKNOWN** = not checked. Paths under `src/msb_v3/`.

## Headline (FACT, unchanged since JOB-011 except where marked DRIFT)
Two governed action paths, not one:
| Path | Code | Gates | Full brakes (`Guard.check_run`)? |
|---|---|---|---|
| Governed tool path (chat/agent tool calls) | `tools/runtime.py::_run_governed` (l.102) | unknown-tool → **kill switch** → approval → capability → executor-exists → execute → audit | No |
| Flywheel path | `flywheel/engine.py` (l.214, 273) | `Guard.check_run` at each stage transition | Yes |
`Guard.check_run` other callers: `tools/executors.py:334` (vault.promote_draft), `api/governance.py:216` (drill endpoint).

**DRIFT vs JOB-011 (FACT):** the tool path now checks the kill switch first (`_killswitch_block_reason`, runtime.py:71, called at :128, verdict `"blocked"`). JOB-011 said the kill switch was flywheel-only. Also `_audit_append` docstring (l.50) still lists 5 verdicts but `"blocked"` is emitted — minor doc drift.

## Blueprint chain → code
| Blueprint stage | Real code | Status |
|---|---|---|
| Intelligence/Proposal | `agent/planner.py`, `agent/intent.py`, `agent/dag.py`, `agent/execution_loop.py`; entry `api/chat.py`, `openai_compat.py`, `moie.py`, `research.py` | LIVE-PATH |
| Typed action proposal | none — tool args arrive as raw `**kwargs`/`Dict` into `_run_governed` | **ABSENT** (JOB-011; not re-grepped for a new model — INFERENCE) |
| Decision object | `governance/decision.py` (`DecisionValue`: ALLOW/REVIEW/BLOCK/UNKNOWN; UNKNOWN first-class) — used by capability resolver, action gate, receipt, shadow mode | EXISTS; live-path wiring of every field = UNKNOWN (docstring says some fields are placeholders "until later phases") |
| Capability resolution | `governance/capability_resolver.py`, `capability_registry.py` | EXISTS; whether `_run_governed` uses the resolver vs the plain `required_capabilities ⊆ granted` check = plain check (FACT l.137) |
| Authorization | approval queue `governance/approval.py`; on tool path a caller-supplied `approved_tools` set (runtime.py:173) | LIVE-PATH but see limitation L3 |
| Capability grant | `granted_capabilities` from request `context` (runtime.py:172), default empty | LIVE-PATH; grant source is the caller |
| Provider contract | `agent/contract.py::ProviderContract` (l.38) + `validate_contract` (l.103) validates provider *specs*; `conversation/task_contract.py`, `conversation/executor.py:294,477` call a *different* `validate_contract` on task DAG entries | Not on the per-tool-call path |
| Execution | `tools/executors.py`, dispatched by name via `getattr(executors, …)` (runtime.py:~148) | LIVE-PATH |
| Verification | `agent/verify.py::verify_task` (l.117) — heuristic checks: search hits, synthesis non-empty, file written, heading present | LIVE for the local agent slice; checks are shallow output checks (INFERENCE from function names; bodies not audited) |
| Evidence | `evidence/spine.py` hash-chained `DecisionEvidence` (`compute_content_hash` l.181, `verify_chain` l.309); `evidence/receipt.py` | EXISTS; per-tool-call evidence goes via AuditChain instead |
| Audit | `_audit_append` → `msb_ledger.chain_anchor.anchored_chain_from_env().append("tools", …)` (best-effort) | LIVE-PATH, **non-fatal on failure** |

## State model
Persisted state: `data/governance/governance.db`, `data/flywheel/turns.db`, `data/runtime/cron.db`, `data/msb_v3.db` (JOB-011; paths not re-verified). **No explicit state-machine object for the blueprint's RECEIVED→…→RECEIPTED lifecycle (FACT by absence in `_run_governed`: it returns a status *string* like `[denied]…`; only the audit verdict string is machine-readable).** Error taxonomy is stringly-typed prefixes on the tool path.

## Not examined (UNKNOWN)
Taint tracking (no dedicated module found by JOB-011; not re-searched), Ouroboros governor internals, `guardrails/`, `triumvirate/`, `wrongness/`, `harnesses/`, `moie/` behavior, the `msb_ledger` package (outside `src/msb_v3`, so anchor/chain integrity is unaudited).
