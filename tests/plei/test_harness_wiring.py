"""A governed endpoint must not run when its governance was not wired.

`_gate_step` in `plei/harness/bridge.py` maps "no gate available" to SAFE. That
is a defensible default for a library call, and it is disclosed in the verdict
reason — but it means a swallowed `ActionGate()` in `/plei/execute` turns a plan
the endpoint advertises as "Gates every step through the ActionGate" into an
ungated run where every step passes.

So the wiring failures are tested as behaviour, not as logging:

* the gate is a refusal (503), not a degradation;
* MoIE and the evidence spine are degradations, and the caller is told;
* every failure leaves a findable log line instead of a bare `pass`.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from fastapi import HTTPException

from msb_v3.plei import api as plei_api


class _FakeStage:
    value = "RESEARCH"


class _FakeLifecycle:
    stage = _FakeStage()
    confidence = 0.5


class _FakeReport:
    ok = True


def _prepared(gate: Any, moie: Any, spine: Any) -> tuple[Any, ...]:
    """The tuple `_execute_prepare_sync` returns, with the wiring under test."""
    return (object(), _FakeLifecycle(), object(), {}, gate, moie, spine)


# ---------------------------------------------------------------------------
# The helper that replaced four silent `pass` blocks
# ---------------------------------------------------------------------------


def test_wire_component_returns_a_constructible_component():
    assert plei_api._wire_component("Path", "pathlib", "Path", consequence="n/a") is not None


def test_wire_component_logs_and_returns_none_instead_of_passing(caplog):
    with caplog.at_level(logging.WARNING, logger=plei_api.__name__):
        got = plei_api._wire_component(
            "ActionGate",
            "msb_v3.not_a_real_module",
            "ActionGate",
            consequence="/plei/execute will refuse to run ungated",
        )

    assert got is None
    assert "ActionGate unavailable" in caplog.text
    assert "ModuleNotFoundError" in caplog.text, "the diagnosis must name the failure"
    assert "/plei/execute will refuse to run ungated" in caplog.text, (
        "the log must say what the missing component costs"
    )


# ---------------------------------------------------------------------------
# The endpoint's contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_refuses_when_the_gate_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(
        plei_api,
        "_execute_prepare_sync",
        lambda root: _prepared(None, object(), object()),
    )

    with pytest.raises(HTTPException) as err:
        await plei_api.plei_execute(project_root=str(tmp_path), session="wiring-test")

    assert err.value.status_code == 503
    assert "ungated" in err.value.detail
    assert "SAFE" in err.value.detail, (
        "the refusal should say why: no gate means every step defaults to SAFE"
    )


@pytest.mark.asyncio
async def test_execute_discloses_missing_moie_and_spine(monkeypatch, tmp_path):
    from msb_v3.plei.harness import bridge, evidence_loop

    async def _fake_execute_plan(plan, **kwargs):
        return _FakeReport()

    monkeypatch.setattr(
        plei_api,
        "_execute_prepare_sync",
        lambda root: _prepared(object(), None, None),
    )
    monkeypatch.setattr(bridge, "execute_plan", _fake_execute_plan)
    monkeypatch.setattr(bridge, "execution_report_as_dict", lambda report: {"ok": True})
    monkeypatch.setattr(evidence_loop, "run_evidence_loop", lambda *a, **k: object())
    monkeypatch.setattr(evidence_loop, "loop_result_as_dict", lambda result: {"ok": True})

    out = await plei_api.plei_execute(project_root=str(tmp_path), session="wiring-test")

    assert out["degraded"] == ["moie", "evidence_spine"], (
        "a run that claimed verification and evidence without either must say so"
    )


@pytest.mark.asyncio
async def test_execute_omits_degraded_when_everything_is_wired(monkeypatch, tmp_path):
    from msb_v3.plei.harness import bridge, evidence_loop

    async def _fake_execute_plan(plan, **kwargs):
        return _FakeReport()

    monkeypatch.setattr(
        plei_api,
        "_execute_prepare_sync",
        lambda root: _prepared(object(), object(), object()),
    )
    monkeypatch.setattr(bridge, "execute_plan", _fake_execute_plan)
    monkeypatch.setattr(bridge, "execution_report_as_dict", lambda report: {"ok": True})
    monkeypatch.setattr(evidence_loop, "run_evidence_loop", lambda *a, **k: object())
    monkeypatch.setattr(evidence_loop, "loop_result_as_dict", lambda result: {"ok": True})

    out = await plei_api.plei_execute(project_root=str(tmp_path), session="wiring-test")

    assert "degraded" not in out, "the happy path's response shape is unchanged"
