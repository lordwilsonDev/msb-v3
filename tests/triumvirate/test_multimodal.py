"""Tests for Triumvirate Phase 6 — Multimodal interfaces."""
from __future__ import annotations

from msb_v3.triumvirate.multimodal_interfaces import HapticHeartbeat, SpeechFunctions, VisionClaw


def test_vision_claw_capture_returns_stub():
    vc = VisionClaw()
    resp = vc.capture_screen()
    assert resp["status"] == "stub"
    assert resp["platform"] == "macOS"


def test_vision_claw_overlay():
    vc = VisionClaw()
    resp = vc.overlay({"text": "ok"})
    assert resp["window"] == "transparent-overlay"


def test_haptic_heartbeat_patterns():
    hh = HapticHeartbeat()
    assert hh.map_pattern(90.0) == "steady-pulse"
    assert hh.map_pattern(70.0) == "double-tap"
    assert hh.map_pattern(30.0) == "rapid-vibration"


def test_speech_map_command_deploy_canary():
    sf = SpeechFunctions()
    result = sf.map_command("deploy the latest canary")
    assert result["endpoint"] == "/research/assistant/run"


def test_speech_map_command_research():
    sf = SpeechFunctions()
    result = sf.map_command("research sovereign AI")
    assert result["endpoint"] == "/research/assistant/run"


def test_speech_map_command_fallback():
    sf = SpeechFunctions()
    result = sf.map_command("say hello")
    assert result["endpoint"] == "/chat"
