"""Tests for VoiceSession latency breakdown."""

from __future__ import annotations

from msb_v3.speech.response import VoiceResponder
from msb_v3.speech.safety import VoiceSession


class TestVoiceSessionLatency:
    def setup_method(self):
        self.responder = VoiceResponder(speak_aloud=False)

    def test_session_has_latency_fields(self):
        session = self.responder.respond_with_session("System status")
        assert hasattr(session, "latency_intent_ms")
        assert hasattr(session, "latency_policy_ms")
        assert hasattr(session, "latency_execute_ms")
        assert hasattr(session, "latency_tts_ms")
        assert hasattr(session, "latency_total_ms")

    def test_session_latency_non_negative(self):
        session = self.responder.respond_with_session("System status")
        assert session.latency_intent_ms >= 0
        assert session.latency_policy_ms >= 0
        assert session.latency_execute_ms >= 0
        assert session.latency_tts_ms >= 0
        assert session.latency_total_ms >= 0

    def test_session_total_ge_parts(self):
        session = self.responder.respond_with_session("System status")
        parts = (
            session.latency_intent_ms
            + session.latency_policy_ms
            + session.latency_execute_ms
            + session.latency_tts_ms
        )
        assert session.latency_total_ms >= parts - 1.0  # allow 1ms rounding

    def test_session_populates_all_fields(self):
        session = self.responder.respond_with_session("System status")
        assert session.transcript_text == "System status"
        assert session.intent_endpoint == "/system/health"
        assert session.risk_level == "LOW"
        assert session.policy_action == "ALLOW"
        assert session.executed is True
        assert session.response_text == "Checking system status."

    def test_session_high_risk_not_executed(self):
        session = self.responder.respond_with_session("Deploy the canary release")
        assert session.risk_level == "HIGH"
        assert session.policy_action == "CONFIRM"
        assert session.executed is False
        assert session.requires_confirmation is True

    def test_session_confirmed_executed(self):
        session = self.responder.respond_with_session(
            "Kill the loop", confirmed=True
        )
        assert session.risk_level == "CRITICAL"
        assert session.policy_action == "ALLOW"
        assert session.executed is True

    def test_session_serializes(self):
        session = self.responder.respond_with_session("System status")
        d = session.as_dict()
        assert "latency_ms" in d
        assert d["latency_ms"]["intent"] >= 0
        assert d["latency_ms"]["total"] >= 0

    def test_two_sessions_different_ids(self):
        s1 = self.responder.respond_with_session("Status")
        s2 = self.responder.respond_with_session("Status")
        assert s1.session_id != s2.session_id
