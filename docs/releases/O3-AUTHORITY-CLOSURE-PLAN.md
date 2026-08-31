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

## The decision (must be made first)

**Option A — make the gateway the enforced doorway.** `gateway_route()` on the
canonical path passes real `capabilities`, `requires_authorization` reflects
the request, a denied/failed decision **raises** (no `try/except` swallow),
and `test_gateway_canonical.py` is tightened to reject the audit-only model.

**Option B — formally accept dual-governance.** Write the acceptance:
gateway = compute-decision audit, ActionGate = capability enforcement, and
the P3 criterion is met when **every entry path reaches ActionGate** (not
necessarily the gateway). Amend the blueprint criterion with the rationale.
Then the work is proving ActionGate coverage across all 14 paths, not
re-routing through the gateway.

Option B is lower-risk and matches how the code already works; Option A is
what the blueprint literally asks for. Pick before touching code.

## The 14-path adversarial matrix

For each entry path: attempt a capability invocation and record ALLOW
(traversed authority) / DENY / **UNKNOWN** (bug — blocks closure).

| # | Entry path | Reaches ActionGate? | Reaches gateway? | Status |
|---|---|---|---|---|
| 1 | `POST /agent/handle` (canonical) | yes (DAG → ActionGate) | audit ping only | needs Option A/B call |
| 2 | `POST /chat` | ? | ? | UNKNOWN |
| 3 | MCP bridge (`/mcp/proxy`) | ? | ? | UNKNOWN |
| 4 | `cron` scheduled job | ? | ? | UNKNOWN |
| 5 | `wake` resident loop | ? | ? | UNKNOWN |
| 6 | `POST /hook/<id>` webhook | ? | ? | UNKNOWN |
| 7 | `automation` brain (n8n/Make/GHL) | ? | ? | UNKNOWN |
| 8 | `factory` pipeline | ? | ? | UNKNOWN |
| 9 | `flywheel` turn | ? | ? | UNKNOWN |
| 10 | provider call (direct `resolve_client`) | ? | ? | UNKNOWN |
| 11 | `replay` engine | ? | ? | UNKNOWN |
| 12 | internal import → direct tool-registry call | ? | ? | UNKNOWN |
| 13 | background task (asyncio) | ? | ? | UNKNOWN |
| 14 | `/v1` OpenAI-compat adapter | ? | ? | UNKNOWN |

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
