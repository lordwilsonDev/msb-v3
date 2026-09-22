# Provider plugins — dropping in a worker

A worker reaches msb-v3 by **registration**, not by editing the seam. The seam
lives in `src/msb_v3/agent/providers.py` and has the four roles a swappable
capability needs: the Definition (`AgentProvider`), the Providers (the workers),
the Consumer (code that calls the Definition) and the Registry
(`ProviderRegistry`). Two invariants make it real, and both are enforced by
tests rather than by convention:

1. **A consumer never names a provider.** Consumers import `ProviderRegistry`
   and select through it. Naming a concrete provider in consumer code is the
   seam leak `tests/contracts/test_interchangeability.py` fails on.
2. **A declared capability is a trust grant, not a description.** Validated
   against the two tables that own the vocabulary; see
   [Capabilities](#capabilities-what-you-may-declare) below.

This document is the path for a system that arrives *after* the seam was built:
one module, one configuration line, and the Registry routes to it without a
change to `default_providers()` and without any consumer learning a new name.
(`unified-architecture §7`; the seam's acceptance condition is
`unified-architecture §31 item 14`.)

## Three ways a worker gets in

| Route | Who decides | Where it lives |
|---|---|---|
| Built-in | The seam's own file | `default_providers()` in `agent/providers.py` |
| **Plugin** | The operator, by configuration | `MSB_PROVIDER_PLUGINS`, loaded by `load_provider_plugins()` |
| Capability grant | The operator, by registration | `agent/identity.py` — a worker holds only what its identity was granted |

Dropping a new system in normally means the second route, and nothing else.
Reach for the first only when the worker belongs to msb-v3 itself.

## Quick start

A conforming worker is one module and one class. Everything below is the
minimum the loader will accept:

```python
# my_pkg/worker.py
import shutil

from msb_v3.agent.providers import AgentProvider, ProviderResult, ProviderSpec


class MyWorker(AgentProvider):
    def __init__(self) -> None:
        self.spec = ProviderSpec(
            provider_id="grpc.my-worker",   # stable id; also the identity in evidence
            display_name="My worker",
            kind="grpc",                     # the routing key consumers select on
            command=("my-worker",),          # empty tuple if it is not a subprocess
            capabilities=(),                 # see the trust boundary below
            max_risk_tier=4,                 # honest: what it may touch, not what it wants
            timeout_s=300.0,
        )

    def available(self) -> bool:
        """True only when it can actually run right now. Probe, do not assume."""
        return shutil.which("my-worker") is not None

    def unavailable_reason(self) -> str:
        return "" if self.available() else "my-worker is not on PATH"

    async def execute(self, goal, *, context=None, session="default") -> ProviderResult:
        ...


WORKER = MyWorker()   # an instance, or a zero-arg factory returning one
```

Then name it:

```bash
export MSB_PROVIDER_PLUGINS="my_pkg.worker:WORKER"   # comma-separated for several
```

And check it registered rather than assuming:

```bash
python - <<'PY'
from msb_v3.agent.providers import ProviderRegistry

registry = ProviderRegistry()
for row in registry.list():
    print(f"{row['provider_id']:22} kind={row['kind']:6} available={row['available']}")
for failure in registry.load_failures():
    print(f"REFUSED {failure.source}: {failure.reason}")
PY
```

`load_failures()` is the important half. A configured worker that does not load
is a substitution hazard — the Registry would route to whatever else is
available and nothing would say the configured one is missing — so every refusal
is recorded **and** logged once at ERROR.

## What the loader checks

Fail-closed: anything not clearly a conforming worker is refused, and the reason
names the missing piece so a registration can be fixed without reading the
loader.

| Requirement | Refusal reason you will see |
|---|---|
| `spec` exposing `provider_id`, `kind`, `capabilities`, `max_risk_tier`, `timeout_s` | ProviderSpec has no `x` / no spec attribute |
| Non-empty string `provider_id` and `kind` | `must be a non-empty string` |
| `max_risk_tier` in 1..4 | `max_risk_tier 9 is outside 1..4` |
| Positive `timeout_s`; `command` and `capabilities` as tuples | `timeout_s ... must be positive`, `command must be a tuple` |
| Capabilities from a known table | declares `['teleport'], which no capability table knows` |
| Callable `available()`, `unavailable_reason()`, `execute()` | available() is missing or not callable |
| One id per plugin | `provider_id 'x' is already registered by another plugin` |
| An importable entry point | `ModuleNotFoundError: No module named 'my_pkg'` |

A bad registration is never an outage: the built-ins keep working and the
refusal is recorded.

## Replacement versus addition

- **Same `provider_id` as a built-in → it replaces it, in place.** Selection is
  registration order, so the plugin takes the built-in's *position*; an override
  routes immediately and `registry.list()` shows one row for that id, not two.
- **New `provider_id` → appended after the built-ins**, in configured order.
- **Two plugins claiming one id → the second is refused.** Resolving that by
  import order would be invisible, and a routing change nobody can see is worse
  than a refusal somebody can.

## Capabilities: what you may declare

Almost always: **nothing.**

A declared capability is a trust grant. The CLI isolation model is explicit that
a subprocess worker on the operator's account holds none until an operator
registers scoped ones — "no implicit trust" — which is why
`CliAgentProvider.spec.capabilities` is empty and routing uses `kind`.
Declaring a capability to make a worker *describable* would be a privilege
escalation dressed as metadata
(`tests/integrations/test_cli_provider_isolation.py`).

When you do declare, the name must come from one of the two tables that own the
vocabulary, and the loader refuses anything else:

| Table | Names |
|---|---|
| `msb_v3.agent.safety.TOOL_CAPABILITY` | governed tool names (`search_query`, `chat`, `vault_write`, …) |
| `tools/registry.py` ToolDefs' `required_capabilities` | tool capability ids (`vault.write`, `factory.run`, …) |

A name in neither is refused because `msb_v3.governance.capability_registry`
resolves unknown ids to `None` by design: it would look registered while
resolving to nothing, so nothing could ever gate it.

## Routing: how a consumer finds you

Consumers pick a worker either by **kind** or by **capability**:

```python
registry.select(required_capabilities=("factory.run",))   # capable + within tier
[p for p in registry.select() if p.spec.kind == "cli"]    # by category
```

The factory routes on kind (`factory/builders.py::_WORKER_KIND = "cli"`). A new
kind is allowed, and the loader logs it at INFO — because no consumer routes on
a kind it has not been taught, and a worker that is registered but never chosen
should be a visible fact rather than a silent one.

## Checklist before you drop one in

- [ ] One module, one conforming `ProviderSpec`, exported as an instance or a factory.
- [ ] `available()` probes reality and `unavailable_reason()` says why not — never `True` plus a failure at call time.
- [ ] `max_risk_tier` reflects what it can touch. A subprocess on the operator's account is tier 4.
- [ ] `capabilities=()` unless an operator is deliberately granting something from a known table.
- [ ] A swap test: register it, show a consumer reaching it, with no consumer edit.
- [ ] A dated entry in `docs/blueprints/convergence-to-12/v4-parking-lot.md` if this is a new subsystem entering the shipping surface — the v3 contract's expansion freeze requires the written exception.

## Where to look next

| For | Read |
|---|---|
| The seam's contract and its conformance suite | `tests/contracts/test_provider_contract.py` |
| Consumer-side routing, enforced by parsing consumers | `tests/contracts/test_interchangeability.py` |
| Registration behaviour, refusals, and the drop-in swap | `tests/contracts/test_provider_plugins.py` |
| Why subprocess workers hold no capabilities | `tests/integrations/test_cli_provider_isolation.py` |
| Worker identity and granted capabilities | `src/msb_v3/agent/identity.py` |
