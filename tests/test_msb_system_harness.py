"""Tests for the read-only MSB system archaeology harness."""

from __future__ import annotations

import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "msb-system-harness.py"


def load_harness():
    return runpy.run_path(str(SCRIPT))


def test_harness_is_importable() -> None:
    assert SCRIPT.is_file()
    module = load_harness()
    assert callable(module["report"])
    assert callable(module["markdown"])


def test_canonical_path_contract() -> None:
    module = load_harness()
    paths = module["CANONICAL_PATHS"]
    assert "src/msb_v3/agent/handle.py" in paths
    assert "src/msb_v3/gateway/route.py" in paths
    assert "src/msb_v3/evidence/spine.py" in paths


def test_report_encodes_core_interpretation_rules() -> None:
    data = load_harness()["report"]()
    rules = data["interpretation_rules"]
    assert rules["unknown_is_not_safe"]
    assert rules["model_is_not_authority"]
    assert rules["proposed_is_not_implemented"]


def test_report_finds_core_surface_and_gates() -> None:
    data = load_harness()["report"]()
    assert data["packages"]
    assert "agent" in data["surface_map"]
    assert data["evidence"]["claims_gate"]
    assert data["evidence"]["production_gate"]


def test_markdown_projection_contains_authority_map() -> None:
    module = load_harness()
    rendered = module["markdown"](module["report"]())
    assert "# MSB-v3 System Harness Report" in rendered
    assert "Canonical control path" in rendered
    assert "Authority map" in rendered
