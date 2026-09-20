# KNOWN LIMITATIONS @ 7cb2f7f — candidate invariants vs code  (R0 candidate)

Purpose: for each blueprint §7 invariant, what enforces it on the live tool path *today*. FACT = read at cited line; INFERENCE = reasoned, not executed. **No invariant below has been adversarially tested by this job; "enforced" means code exists, not that it survives attack.**

| # | Invariant | Live-path enforcement today | Assessment |
|---|---|---|---|
| I1 Authorization | EXECUTION→AUTHORIZED | `_run_governed`: kill switch (l.128), approval-required check (l.133), capability check (l.137) run before `executor(...)` (l.148) | Structurally present for tools flagged `approval_required` or with `required_capabilities`. **A tool with `approval_required=False` and empty `required_capabilities` executes with no authorization (FACT: both checks are conditional on td fields).** Whether such tools exist = UNKNOWN (not enumerated). |
| I2 Capability | REQUIRED ⊆ GRANTED | l.137 exact subset test; `granted` default empty (l.172) | Enforced for declared caps. Correctness depends on `required_capabilities` being complete per tool (UNKNOWN). |
| I3 Verification→observable evidence | `agent/verify.py` heuristic output checks | Weak/shallow (INFERENCE). Nothing ties "VERIFIED" to independent observation of side effects. |
| I4 Fail-closed | unknown tool → error (l.121); missing approval/caps → refuse | Enforced for those paths. **Audit failure is fail-OPEN by design** (`_audit_append` docstring l.~40: "never fatal"), so an execution can occur with no evidence record. |
| I5 Evidence chain | `evidence/spine.py` hash chain + `verify_chain`; tool calls use AuditChain via `msb_ledger` | Chain exists; per-call append is best-effort (see I4); `msb_ledger` not audited. Tool content deliberately excluded from audit payload (only tool id/args metadata) → evidence is a record of *the call*, not of the *result*. INFERENCE. |
| I6 Identity | AUTHENTICATED_ACTOR | **No actor/principal notion in `_run_governed`** (grep of `actor|authenticat|principal` in tools/runtime.py, governance/approval.py, governance/decision.py returned nothing relevant). `granted_capabilities` and `approved_tools` come straight from the request `context` dict (runtime.py:172-173). Anything that can set context can self-authorize. Upstream API auth (e.g. the factory-gate "live_auth" 200/401 check) protects the HTTP boundary, not this layer. | **Likely the biggest gap vs blueprint.** Mark: INFERENCE, needs an attack test. |
| I7 Provider contract | `ProviderContract` validated at provider-spec level | Not checked per execution (INFERENCE); `_run_governed` never references it. |

## Other limitations (FACT unless marked)
- No typed `ActionProposal`; args are raw dicts; each executor validates its own (JOB-011).
- Tool-path error taxonomy is strings, not enums; `SEMANTIC_ERROR`, dedicated `TAINT_ERROR`, `POSTCONDITION_ERROR` absent (JOB-011).
- No post-condition check on resulting state (JOB-011).
- Full brake stack (budget, ouroboros governor) gates only flywheel + vault.promote_draft + drill endpoint, not general tool calls.
- Dirty-tree drift: daily jobs rewrite `artifacts/hygiene/daily_gate_events.jsonl` and `.plei/calibration.jsonl` (tracked). A stash holds pre-R0 versions; see COMMIT.txt.
- Hardware label mismatch: blueprint says "Mac mini M1"; this machine reports **Apple M4**, 16 GiB, macOS 26.6.2. Blueprint header should be corrected (Wilson).
- Test/CI evidence is the project's own gates; none of it is independent of the system under study (blueprint §14 caveat).
- Qdrant/Ollama/server availability affects some tests (see TEST_RESULTS.md).

## Implication for the research plan (INFERENCE, for Wilson)
H1 (unauthorized-execution reduction) is testable now on I1/I2 and the kill-switch path, but the *identity* invariant (I6) is not implemented at the kernel layer, and audit is fail-open (I4/I5). Either those are scoped out of Claim 2 explicitly, or the "governance kernel" spec must define where identity and mandatory-evidence enforcement live. This is a spec decision, not something to patch during the R0 freeze.
