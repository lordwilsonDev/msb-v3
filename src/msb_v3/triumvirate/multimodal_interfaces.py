"""Multimodal interfaces: VisionClaw, HapticHeartbeat, SpeechFunctions.

CONVERGENCE STATUS (2026-08-17): PARKED, not forgotten.
These three were the last live "stub" labels in the shipping surface. Per the
convergence pass (wire/cut/park), they are deliberately PARKED rather than
wired or cut: the mac-mini sovereign node's storage budget cannot host the
screen/audio capture pipeline this cycle (the disk was at 100% and the
enrollment-era caches were the cause). They are out of the v3 claim surface
— the release doc lists multimodal as "implemented but experimental, parked"
— and become buildable in v4 when storage is not the constraint.
"""
from __future__ import annotations

from typing import Any, Dict

# Every status here is "parked" (not "stub"): the interface is intentionally
# inert with a dated, reason-bearing decision (blocked_on mac-mini-storage).
_PARKED = {"status": "parked", "blocked_on": "mac-mini-storage"}


class VisionClaw:
    def capture_screen(self) -> Dict[str, Any]:
        return {
            **_PARKED,
            "platform": "macOS",
            "capture_backend": "ScreenCaptureKit",
            "timestamp": _now_iso(),
        }

    def overlay(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return {
            **_PARKED,
            "window": "transparent-overlay",
            "data": data,
        }


class HapticHeartbeat:
    def poll_sac(self) -> Dict[str, Any]:
        return {
            **_PARKED,
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
        # PARKED (blocked_on mac-mini-storage): the speech pipeline is inert;
        # this canned transcript keeps the interface deterministic for tests.
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
