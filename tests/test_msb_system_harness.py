"""Tests for the read-only MSB system archaeology harness."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "msb-system-harness.py"

def load_harness():
    spec = importlib.util.spec_from_file_location("msb_system_harness", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def test_harness_is_importable() -> None:
    assert SCRIPT.is_file()
    module = load_harness()
    assert callable(module.report)
    assert callable(module.markdown)

def test_canonical_path_contract() -> None:
    module = load_harness()
    assert "src/msb_v3/agent/handle.py" in module.CANONICAL_PATHS
    assert "src/msb_v3/gateway/route.py" in module.CANONICAL_PATHS
    assert "src/msb_v3/evidence/spine.py" in module.CANONICAL_PATHS

def test_report_encodes_core_interpretation_rules() -> None:
    module = load_harness()
    data = module.report()
    rules = data["interpretation_rules"]
    assert rules["unknown_is_not_safe"]
    assert rules["model_is_not_authority"]
    assert rules["proposed_is_not_implemented"]

def test_report_finds_core_surface_and_gates() -> None:
    module = load_harness()
    data = module.report()
    assert data["packages"]
    assert "agent" in data["surface_map"]
    assert data["evidence"]["claims_gate"]
    assert data["evidence"]["production_gate"]

def test_markdown_projection_contains_authority_map() -> None:
    module = load_harness()
    text = module.markdown(module.report())
    assert "# MSB-v3 System Harness Report" in text
    assert "Canonical control path" in text
    assert "Authority map" in text
