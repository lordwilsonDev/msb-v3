"""Silent-failure visibility — a swallowed failure must still say why.

Before 2026-09-23 a set of `except Exception: pass` handlers under `plei/`
discarded the failure *and its reason*. The swallow itself was usually
correct — PLEI is an analysis layer, and a probe that cannot read one source
should not fail the whole twin — but the effect was that a lost fact left no
trace anywhere. A failure mode missing from the risk report, or a prediction
that never reached the calibration store, looked exactly like a probe that
found nothing.

These tests pin both halves of the corrected contract:

    1. the failure still does not propagate (the loop must not break), and
    2. the reason reaches the log (so the loss is observable).

Asserting only (1) would have passed against the old, silent code; asserting
only (2) would not prove the caller survives. Both are required.

The blanket `except Exception:` at each of these sites is annotated
`# noqa: BLE001` with a stated reason rather than narrowed, which is the
distinction the rule exists to force.
"""

from __future__ import annotations

import logging

from msb_v3.plei.harness.bridge import ExecutionReport
from msb_v3.plei.harness.evidence_loop import TwinDelta, _auto_record_outcome
from msb_v3.plei.orchestrator import _extract_pyproject_version


def test_failed_calibration_recording_surfaces_instead_of_vanishing(
    monkeypatch, caplog
) -> None:
    """A recording failure must not be silent — it leaves calibration short."""
    from msb_v3.plei.calibration import store as calibration_store

    class _ExplodingStore:
        """A store that cannot be constructed — the failure under test."""

        def __init__(self, *args: object, **kwargs: object) -> None:
            raise RuntimeError("calibration store unavailable")

    monkeypatch.setattr(calibration_store, "CalibrationStore", _ExplodingStore)

    report = ExecutionReport(
        plan_id="plan:test",
        ok=True,
        total_steps=1,
        completed_steps=1,
        failed_steps=0,
        blocked_steps=0,
        review_steps=0,
        total_duration_s=120.0,
    )

    with caplog.at_level(
        logging.WARNING, logger="msb_v3.plei.harness.evidence_loop"
    ):
        # Half 1: the failure does not propagate. Recording happens after the
        # run has already succeeded, so it must not turn a good run into an
        # error.
        _auto_record_outcome(report, TwinDelta())

    # Half 2: the reason is still recorded somewhere.
    assert "calibration outcome not recorded" in caplog.text
    assert "calibration store unavailable" in caplog.text


def test_unreadable_pyproject_degrades_to_unknown_and_says_so(
    tmp_path, caplog
) -> None:
    """A stated fact that reads "unknown" is worse than not stating it."""
    with caplog.at_level(logging.DEBUG, logger="msb_v3.plei.orchestrator"):
        # tmp_path holds no pyproject.toml, so the read fails.
        assert _extract_pyproject_version(tmp_path) == "unknown"

    assert "pyproject version unreadable" in caplog.text
