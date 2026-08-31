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

| # | Entry path | Reaches ActionGate? | How | Status |
|---|---|---|---|---|
| 1 | `POST /agent/handle` | yes | `api/agent.py:81` → `handle()` → `SafeProvider`+`execute_graph` | **ALLOW-through-authority** |
| 2 | provider call `local.slice` / `api.anthropic` / `api.deepseek` | yes | `agent/providers.py:195,489,553` delegate to `handle()` | **ALLOW-through-authority** |
| 3 | `integrations/openbot` | yes | `integrations/openbot.py:110` → `handle()` | **ALLOW-through-authority** |
| 4 | `POST /chat` | ? | trace `api/chat.py` / `harnesses/base.py` (gateway route present; ActionGate?) | UNKNOWN |
| 5 | MCP bridge (`/mcp/proxy`) | ? | trace `api/mcp_bridge.py` — proxies to `/chat`, `/agent`, `/memory` | UNKNOWN |
| 6 | `cron` scheduled job | ? | trace `msb_v3/cron` job runner | UNKNOWN |
| 7 | `wake` resident loop | ? | trace `msb_v3/wake` cycle runner | UNKNOWN |
| 8 | `POST /hook/<id>` webhook | ? | trace `api/hook.py` → wake inbox | UNKNOWN |
| 9 | `automation` brain | ? | trace `msb_v3/automation` (external side effects — H5 also) | UNKNOWN |
| 10 | `factory` pipeline | ? | trace `msb_v3/factory` (FROZEN) — build/test/review | UNKNOWN |
| 11 | `flywheel` turn | ? | trace `msb_v3/flywheel` (behind Phase-0B brakes) | UNKNOWN |
| 12 | `replay` engine | ? | `msb_v3/replay` (FROZEN) — replays recorded runs; likely READ-ONLY | UNKNOWN |
| 13 | internal import → `tools` registry direct call | ? | grep for `run_gated` bypass — any caller of `tools/executors` not via `run_gated` | UNKNOWN |
| 14 | `/v1` OpenAI-compat adapter | ? | trace `api/openai_compat.py` — proxies to `/chat`? | UNKNOWN |

**Next-session task:** resolve rows 4–14 by reading each module's call graph to
the first capability execution. 3/14 confirmed ALLOW-through-authority.

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
