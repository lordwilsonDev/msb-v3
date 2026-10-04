"""Regression: the test suite must not write the tracked .plei/calibration.jsonl.

twin_summary() auto-records a calibration PREDICTION through a no-arg
CalibrationStore(); before tests/conftest.py::_isolate_plei_calibration that
store resolved to the repo's tracked file and every test run dirtied it.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from msb_v3.plei.calibration import store as store_mod
from msb_v3.plei.calibration.store import CalibrationStore
from msb_v3.plei.orchestrator import ingest_all, twin_summary

from .conftest import PLEI_ROOT

REPO_CALIBRATION = PLEI_ROOT / ".plei" / "calibration.jsonl"


def _digest(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def test_default_store_is_redirected_to_tmp(tmp_path: Path) -> None:
    store = CalibrationStore()
    assert store.path.is_relative_to(tmp_path.resolve())
    assert store.path != REPO_CALIBRATION.resolve()


def test_explicit_path_still_wins(tmp_path: Path) -> None:
    explicit = tmp_path / "explicit.jsonl"
    assert CalibrationStore(path=explicit).path == explicit.resolve()


def test_real_default_is_unchanged(monkeypatch) -> None:
    # Undo the autouse redirect to check the production default itself.
    monkeypatch.undo()
    assert Path(store_mod.DEFAULT_PATH) == Path(".plei/calibration.jsonl")


def test_twin_summary_leaves_repo_calibration_file_untouched(tmp_path: Path) -> None:
    before = _digest(REPO_CALIBRATION)
    twin_summary(ingest_all(PLEI_ROOT))
    assert _digest(REPO_CALIBRATION) == before
    # ...and the prediction went to the redirected store instead.
    assert CalibrationStore().predictions()
