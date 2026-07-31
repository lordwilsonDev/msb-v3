"""Multimodal interfaces: VisionClaw, HapticHeartbeat, SpeechFunctions."""
from __future__ import annotations

import math
from typing import Any, Dict


class VisionClaw:
    def capture_screen(self) -> Dict[str, Any]:
        return {
            "status": "stub",
            "platform": "macOS",
            "capture_backend": "ScreenCaptureKit",
            "timestamp": _now_iso(),
        }

    def overlay(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "status": "stub",
            "window": "transparent-overlay",
            "data": data,
        }


class HapticHeartbeat:
    def poll_sac(self) -> Dict[str, Any]:
        return {
            "status": "stub",
            "sas": 100.0,
            "pattern": "steady-pulse",
            "platform": "CoreHaptics",
        }

    def map_pattern(self, sas: float) -> str:
        if sas > 80:
            return "steady-pulse"
        if sas >= 60:
            return "double-tap"
        return "rapid-vibration"


class SpeechFunctions:
    def transcribe(self, audio_path: str) -> str:
        return "deploy canary"

    def map_command(self, transcript: str) -> Dict[str, Any]:
        text = (transcript or "").lower()
        if "deploy" in text and "canary" in text:
            return {"endpoint": "/research/assistant/run", "method": "POST", "body": {"topic": "deploy canary"}}
        if "research" in text:
            return {"endpoint": "/research/assistant/run", "method": "POST", "body": {"topic": transcript}}
        return {"endpoint": "/chat", "method": "POST", "body": {"query": transcript}}


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
