"""Drop-in provider registration: a worker arrives by configuration, not by edit.

Every built-in worker is listed in `default_providers()` — the Definition's own
file — so before this, adding one meant editing the seam itself and every
consumer learned a new name. These tests pin the registration path instead: an
operator names a `module:attr` entry point, the loader validates it against the
seam, and `ProviderRegistry` routes to it with no edit to `default_providers()`
and none to any consumer.

The refusals matter as much as the acceptances. A configured worker that fails
to load is a substitution hazard — the Registry would route to whatever else is
available and nothing would say the configured one is missing — so every refusal
is recorded on the registry and logged once.
"""

from __future__ import annotations

import sys
from typing import Any, Tuple

import pytest

from msb_v3.agent.providers import (
    ProviderRegistry,
    default_providers,
    load_provider_plugins,
)
from msb_v3.core.config import settings

_PLUGIN_TEMPLATE = '''
from msb_v3.agent.providers import AgentProvider, ProviderResult, ProviderSpec


class DroppedIn(AgentProvider):
    """A worker that arrives from configuration rather than from this package."""

    def __init__(self):
        self.spec = ProviderSpec(
            provider_id={provider_id!r},
            display_name="dropped-in worker",
            kind={kind!r},
            command=("dropped-in",),
            capabilities={capabilities!r},
            max_risk_tier={max_risk_tier},
            timeout_s=30.0,
        )

    def available(self):
        return True

    def unavailable_reason(self):
        return ""

    async def execute(self, goal, *, context=None, session="default"):
        return ProviderResult(ok=True, output="dropped-in: " + goal)


INSTANCE = DroppedIn()
'''


def _install_plugin(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    *,
    module: str = "dropped_in_worker",
    attr: str = "INSTANCE",
    provider_id: str = "cli.dropped-in",
    kind: str = "cli",
    capabilities: Tuple[str, ...] = (),
    max_risk_tier: int = 4,
    body: str | None = None,
) -> str:
    """Write a worker module into tmp_path and point MSB_PROVIDER_PLUGINS at it.

    Written at test time rather than committed as a fixture, so the drop-in is
    practised the way it will actually happen: a module the seam has never seen.
    """
    (tmp_path / f"{module}.py").write_text(
        body
        if body is not None
        else _PLUGIN_TEMPLATE.format(
            provider_id=provider_id,
            kind=kind,
            capabilities=capabilities,
            max_risk_tier=max_risk_tier,
        ),
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(settings, "provider_plugins", f"{module}:{attr}")
    monkeypatch.delitem(sys.modules, module, raising=False)
    return f"{module}:{attr}"


def test_nothing_configured_leaves_the_registry_exactly_as_built(monkeypatch):
    """The default path must stay untouched — no ambient plugin loading."""
    monkeypatch.setattr(settings, "provider_plugins", "")
    registry = ProviderRegistry()
    assert [p["provider_id"] for p in registry.list()] == [
        p.spec.provider_id for p in default_providers()
    ]
    assert registry.load_failures() == ()


def test_a_configured_worker_is_registered(tmp_path, monkeypatch):
    """The entry point resolves, validates, and appears in the registry."""
    _install_plugin(tmp_path, monkeypatch)
    registry = ProviderRegistry()
    assert registry.load_failures() == ()
    assert "cli.dropped-in" in {p["provider_id"] for p in registry.list()}
    # It is selectable on the same terms as any built-in worker.
    assert registry.get("cli.dropped-in") is not None


def test_a_dropped_in_worker_takes_over_the_factory_with_no_consumer_edit(tmp_path, monkeypatch):
    """The whole point of the seam: replacing a worker is a config act.

    The plugin claims a built-in's id, so it must *replace* it — if both stayed
    registered, the built-in would win registration order and the override would
    silently do nothing.
    """
    from msb_v3.factory.builders import CliAgentBuilder

    _install_plugin(tmp_path, monkeypatch, provider_id="cli.claude", kind="cli")

    builder = CliAgentBuilder()
    assert builder._provider.spec.display_name == "dropped-in worker", (
        f"factory still holds {builder._provider.spec.display_name!r} — the "
        f"override did not route"
    )
    # And the built-in it replaced is gone, not shadowed.
    registry = ProviderRegistry()
    owners = [p["display_name"] for p in registry.list() if p["provider_id"] == "cli.claude"]
    assert owners == ["dropped-in worker"], f"cli.claude resolved to {owners}"


def test_a_worker_declaring_an_unknown_capability_is_refused(tmp_path, monkeypatch):
    """A capability is a trust grant, so an unrecognised name is a refusal.

    Accepting it would produce a name that looks registered while resolving to
    nothing: `governance.capability_registry` returns None for ids it does not
    know, so nothing could gate it.
    """
    _install_plugin(tmp_path, monkeypatch, capabilities=("teleport",))
    registry = ProviderRegistry()
    failures = registry.load_failures()
    assert len(failures) == 1, f"expected one refusal, got {failures}"
    assert "teleport" in failures[0].reason
    assert "cli.dropped-in" not in {p["provider_id"] for p in registry.list()}
    # The built-ins keep working — a bad registration is not an outage.
    assert "cli.claude" in {p["provider_id"] for p in registry.list()}


def test_a_worker_declaring_a_governed_capability_is_accepted(tmp_path, monkeypatch):
    """The check is a vocabulary, not a ban.

    Writing MSB_PROVIDER_PLUGINS is an operator act — the same authority that
    governs the grant — so a plugin may declare a capability the tables know.
    What it may not do is invent one.
    """
    _install_plugin(tmp_path, monkeypatch, capabilities=("chat",))
    registry = ProviderRegistry()
    assert registry.load_failures() == ()
    assert registry.get("cli.dropped-in").spec.capabilities == ("chat",)


@pytest.mark.parametrize(
    "provider_id,kind,max_risk_tier,expected",
    [
        ("", "cli", 4, "provider_id"),
        ("cli.bad", "", 4, "kind"),
        ("cli.bad", "cli", 9, "max_risk_tier"),
    ],
)
def test_a_malformed_spec_is_refused_with_a_fixable_reason(
    tmp_path, monkeypatch, provider_id, kind, max_risk_tier, expected
):
    """The reason must name the missing piece, not just say no."""
    _install_plugin(
        tmp_path,
        monkeypatch,
        provider_id=provider_id,
        kind=kind,
        max_risk_tier=max_risk_tier,
    )
    failures = ProviderRegistry().load_failures()
    assert len(failures) == 1
    assert expected in failures[0].reason


def test_a_missing_entry_point_is_refused_not_raised(tmp_path, monkeypatch):
    """A typo in configuration must be recorded, not crash every registry build.

    The Registry is constructed per `handle()` call, so raising here would turn
    one bad config value into a runtime that cannot serve at all.
    """
    monkeypatch.setattr(settings, "provider_plugins", "no_such_module_xyz:INSTANCE")
    registry = ProviderRegistry()
    failures = registry.load_failures()
    assert len(failures) == 1
    assert "no_such_module_xyz" in failures[0].source
    assert "ModuleNotFoundError" in failures[0].reason


def test_two_workers_claiming_one_id_are_a_configuration_error(tmp_path, monkeypatch):
    """Two plugins, one id: the second is refused rather than winning by import
    order, which nothing would ever reveal."""
    _install_plugin(tmp_path, monkeypatch, module="first_worker", provider_id="cli.dup")
    _install_plugin(tmp_path, monkeypatch, module="second_worker", provider_id="cli.dup")
    monkeypatch.setattr(
        settings, "provider_plugins", "first_worker:INSTANCE,second_worker:INSTANCE"
    )

    registry = ProviderRegistry()
    assert [f.source for f in registry.load_failures()] == ["second_worker:INSTANCE"]
    assert "already registered" in registry.load_failures()[0].reason
    assert [p["provider_id"] for p in registry.list()].count("cli.dup") == 1


def test_loading_is_pure_when_asked_directly(tmp_path, monkeypatch):
    """`load_provider_plugins` is usable on its own, and reports rather than
    mutating anything the caller owns."""
    source = _install_plugin(tmp_path, monkeypatch)
    loaded, failures = load_provider_plugins(existing=default_providers())
    assert failures == ()
    assert [p.spec.provider_id for p in loaded] == ["cli.dropped-in"]
    assert source in provider_specs_under_test()


def provider_specs_under_test() -> Tuple[str, ...]:
    """The configured entry point, read back the way the loader reads it."""
    from msb_v3.agent.providers import provider_plugin_specs

    return provider_plugin_specs()
