"""Tests for Triumvirate Phase 2 — MissionAnchor + STATUS engine."""
from __future__ import annotations

import importlib
import json

import pytest

import msb_v3.triumvirate.mission_anchor as mission_mod


def _reload():
    return importlib.reload(mission_mod)


def test_scope_lock_writes_status(tmp_path, monkeypatch):
    mod = _reload()
    monkeypatch.setattr(mod, "_RUNTIME_ROOT", tmp_path / "triumvirate")
    monkeypatch.setattr(mod, "_STATUS_FILE", tmp_path / "triumvirate" / "STATUS.json")
    anchor = mod.MissionAnchor()
    status = anchor.scope_lock("build cluster", {"mode": "test"})
    assert status["scope_hash"] == mod._goal_signature("build cluster", {"mode": "test"})
    assert status["current_phase"] == "locked"
    assert mod._STATUS_FILE.exists()


def test_update_reflects_new_phase(tmp_path, monkeypatch):
    mod = _reload()
    monkeypatch.setattr(mod, "_RUNTIME_ROOT", tmp_path / "triumvirate")
    monkeypatch.setattr(mod, "_STATUS_FILE", tmp_path / "triumvirate" / "STATUS.json")
    anchor = mod.MissionAnchor()
    anchor.scope_lock("goal")
    updated = anchor.update("executing", 1, 0.1)
    assert updated["current_phase"] == "executing"
    assert updated["iteration_count"] == 1
    assert updated["budget_spent_usd"] == 0.1


def test_verify_valid_when_unchanged(tmp_path, monkeypatch):
    mod = _reload()
    monkeypatch.setattr(mod, "_RUNTIME_ROOT", tmp_path / "triumvirate")
    monkeypatch.setattr(mod, "_STATUS_FILE", tmp_path / "triumvirate" / "STATUS.json")
    anchor = mod.MissionAnchor()
    anchor.scope_lock("goal", {"x": 1})
    result = anchor.verify()
    assert result["valid"] is True


def test_verify_invalid_triggers_circuit_breaker(tmp_path, monkeypatch):
    mod = _reload()
    monkeypatch.setattr(mod, "_RUNTIME_ROOT", tmp_path / "triumvirate")
    monkeypatch.setattr(mod, "_STATUS_FILE", tmp_path / "triumvirate" / "STATUS.json")
    anchor = mod.MissionAnchor()
    anchor.scope_lock("goal", {"x": 1})
    status_path = mod._STATUS_FILE
    data = json.loads(status_path.read_text())
    data["scope_hash"] = "tampered"
    status_path.write_text(json.dumps(data))
    result = anchor.verify()
    assert result["valid"] is False
    status = anchor.read()
    assert status["circuit_breaker"] is True
    assert status["current_phase"] == "paused"


def test_circuit_breaker_manual_trigger(tmp_path, monkeypatch):
    mod = _reload()
    monkeypatch.setattr(mod, "_RUNTIME_ROOT", tmp_path / "triumvirate")
    monkeypatch.setattr(mod, "_STATUS_FILE", tmp_path / "triumvirate" / "STATUS.json")
    anchor = mod.MissionAnchor()
    anchor.scope_lock("goal")
    out = anchor.circuit_breaker_trigger()
    assert out["circuit_breaker"] is True
    assert out["current_phase"] == "paused"


def test_goal_signature_changes_with_parameters():
    assert mission_mod._goal_signature("goal") != mission_mod._goal_signature("goal", {"x": 1})
