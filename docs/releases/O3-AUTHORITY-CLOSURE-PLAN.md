# O3 — Authority Closure Plan (PRODUCTION-CLOSURE-001 P3)

**Status:** SCOPED, not started · **Depends on:** O1 ✅, O2 ✅ (both closed by v0.4.2)
**Estimated:** its own multi-session block — touches every entry path + a CI gate.

---

## The gap, precisely

`gateway_route()` is called on the canonical agent path
(`src/msb_v3/agent/handle.py:823`) but it is **not enforcing**:

```python
try:
    gateway_route(GatewayCall(name="agent.handle",
        capabilities=frozenset(),          # <- nothing to check
        requires_authorization=False,      # <- audit ping, not a gate
        ...), GatewayContext())
except Exception as exc:                    # <- best-effort
    logger.debug("gateway audit entry failed ...")
```

`tests/architecture/test_gateway_canonical.py` **accepts** this — its stated
model is "two orthogonal governance layers (gateway = audit, ActionGate =
enforce); both are acceptable." That is weaker than the blueprint's P3
criterion:

> No production capability can execute without traversing the authoritative
> control boundary. There can be no accidental third state — every path is
> ALLOW-through-authority or DENY.

## The decision — RESOLVED 2026-08-31: Option B

**Option B — dual-governance, `ActionGate` is the enforcement boundary.**
Acceptance + revised invariant: `docs/governance/authority-model.md`.
The P3 criterion is now: *every entry path that can execute a capability
routes it through `ActionGate` (`SafeProvider` / `run_gated`) first; no third
state — `allowed` / `denied` / `approval-required` / `error`.*

## The 14-path adversarial matrix

Status vocab: **ALLOW-through-authority** (execution crosses ActionGate) /
**DENY** / **READ-ONLY** (path cannot execute a capability; documented) /
**UNKNOWN** (bug — blocks closure).

Established so far: **`agent/handle.py::handle()` is the single gated executor**
— the only site that builds `SafeProvider(provider, gate) + execute_graph`
(`handle.py:987,1010`). Any path that runs a capability either routes through
`handle()` or must call `tools/runtime.py::run_gated` directly.

The capability primitives are the functions in `src/msb_v3/tools/executors.py`
(`vault_write`, `vault_delete`, `memory_store`, `codegraph_rename`, …). The
**only** sanctioned way to reach them is `tools/runtime.py::_run_governed`
(capability check → approval check → executor → audit, 5 verdicts:
`allowed` / `denied` / `approval-required` / `unknown` / `error`). The other
boundary is `agent/safety.py::SafeProvider` (DAG path — maps tool→capability,
then gates). `test_authority_boundary.py` scans every `src/` module and fails
if any but `tools/runtime.py` imports `executors`.

| # | Entry path | Class | Evidence |
|---|---|---|---|
| 1 | `POST /agent/handle` | **ALLOW** | `api/agent.py:81` → `handle()` → `SafeProvider(provider, gate)` + `execute_graph` (`handle.py:987,1010`) |
| 2 | in-process providers (`local.slice` / `api.anthropic` / `api.deepseek`) | **ALLOW** | `agent/providers.py:195,489,553` delegate to `handle()` |
| 3 | `integrations/openbot` | **ALLOW** | `openbot.py:110` → `handle()` |
| 4 | `POST /chat` | **ALLOW** | `harnesses/base.py:117` `register_governed_tools(client, …)` → tools wrapped by `_run_governed` before `execute_tool_loop` |
| 5 | `/v1` OpenAI-compat | **ALLOW** | `api/openai_compat.py:281` → `ChatHarness.execute` (= path 4) |
| 6 | MCP bridge `/mcp/proxy` | **ALLOW** | tool calls → `mcp_bridge.py:51 _run_governed_proxy` → `tools.runtime._run_governed`; `match` cases proxy to `/chat` (path 4) or read-only `/status` `/metrics` `/memory`-GET |
| 7 | `POST /hook/<id>` webhook | **READ-ONLY** | `api/hook.py` → `WakeStore` enqueue only; nothing executes at receive |
| 8 | `wake` resident loop | **CONSTRAINED** | `wake/runner.py` turn = bare `DeepSeekClient.chat` (no tool loop, no executor access); automation-plan handoff → path 9's gate |
| 9 | `automation` brain | **CONSTRAINED** | `automation/brain.py` — dry-run by default, creation requires explicit approval, `BudgetLedger` spend cap, durable `Manifest` (status ∈ created/dry_run/blocked/failed) |
| 10 | `cron` scheduled job | **CONSTRAINED** | `cron/actions.py` — fixed `ACTIONS` registry (~7 ops actions, no arbitrary tools); scheduler killswitch/timeout/retry; `action_http_call` host-allowlisted; `requires_approval` jobs |
| 11 | `factory` pipeline | **CONSTRAINED** | operator-auth entry (`/factory`), FROZEN; CLI subprocess exec (`builders.py:124`) is the **C5 accepted-risk** (written waiver 2026-08-26) |
| 12 | `flywheel` turn | **ALLOW** (approval brake) | `flywheel/cli.py` — turn parks at `WAITING_APPROVAL` at build/combine/record until an operator approves (Phase-0B brakes) |
| 13 | `replay` engine | **READ-ONLY** | `replay/engine.py` — `replay_state/decision/task` reconstruct from the audit chain + event log; never re-executes |
| 14 | internal import → tool-registry direct | **ALLOW** | `_run_governed` **is** the registry entry point; `test_authority_boundary.py::test_no_module_reaches_executors_except_the_gate` proves no bypass |

**Zero `UNKNOWN`.** Every path is ALLOW-through-authority, CONSTRAINED (with a
documented narrower authority), or READ-ONLY. Enforced by
`tests/architecture/test_authority_boundary.py` (16 cases; the bypass scanner
is adversarially verified to fail on an injected direct-executor import).

## Work items (after the decision)

1. **Map** — for each path, trace the call graph from entry to the first
   capability/tool execution. Record where authority is (or isn't) crossed.
2. **Close** — for every path that reaches a capability without authority,
   route it (Option A) or add the ActionGate hop (Option B).
3. **Test** — extend `tests/architecture/test_gateway_canonical.py` (or a new
   `test_authority_boundary.py`) with one adversarial case per path, asserting
   ALLOW-through-authority or DENY, never a third state. Make it a **blocking**
   CI job (currently it's a passive architecture check).
4. **Evidence** — fill the matrix above; zero UNKNOWN is the acceptance bar.

## Acceptance

- [ ] Decision (Option A or B) written with rationale.
- [ ] All 14 paths mapped; matrix has zero UNKNOWN.
- [ ] Every path is ALLOW-through-authority or DENY.
- [ ] Adversarial bypass suite exists and is a blocking CI gate.
- [ ] `test_gateway_canonical.py` no longer accepts an un-mediated path.
