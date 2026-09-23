"""Provider-selection shaping must degrade loudly, never silently.

The WorkPlan proceeds without a provider choice when shaping `provider_sel`
fails — that is a *degraded* plan (review item: "governance wiring shouldn't
degrade quietly"), so the failure has to appear in the log with its cause,
the same treatment `_wire_component` and the provider-registry site give theirs.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

from msb_v3.plei import api as plei_api


class _ExplodingProviderId:
    """A primary whose provider_id access blows up mid-shaping."""

    @property
    def provider_id(self) -> str:
        raise RuntimeError("profile handle is dead")


def _good_selection() -> SimpleNamespace:
    return SimpleNamespace(
        primary=SimpleNamespace(provider_id="p1"),
        fallbacks=[SimpleNamespace(provider_id="p2")],
        rationale="because reasons",
    )


def test_wellformed_selection_shapes_without_logging(caplog):
    with caplog.at_level(logging.WARNING, logger=plei_api.__name__):
        out = plei_api._prov_dict_or_none(_good_selection())
    assert out == {
        "primary": {"provider_id": "p1"},
        "fallbacks": [{"provider_id": "p2"}],
        "rationale": "because reasons",
    }
    assert "WARNING" not in [rec.levelname for rec in caplog.records]


def test_shaping_failure_logs_the_cause_and_returns_none(caplog):
    bad = SimpleNamespace(primary=_ExplodingProviderId(), fallbacks=[], rationale="r")
    with caplog.at_level(logging.WARNING, logger=plei_api.__name__):
        out = plei_api._prov_dict_or_none(bad)
    assert out is None, "degraded shape is allowed — the plan continues without providers"
    assert len(caplog.records) == 1, "the degradation must be visible, exactly once"
    text = caplog.text
    assert "RuntimeError" in text, "the diagnosis must name the failure type"
    assert "profile handle is dead" in text, "the diagnosis must carry the cause"
    assert "no provider choice" in text, "the diagnosis must state the consequence"


def test_none_primary_is_not_a_failure(caplog):
    sel = SimpleNamespace(primary=None, fallbacks=[], rationale="none")
    with caplog.at_level(logging.WARNING, logger=plei_api.__name__):
        out = plei_api._prov_dict_or_none(sel)
    assert out == {"primary": None, "fallbacks": [], "rationale": "none"}
    assert caplog.records == []
