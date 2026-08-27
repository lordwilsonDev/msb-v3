"""Tests for voice safety layer — policy gate, risk, confidence, confirmation, audit."""

from __future__ import annotations

from msb_v3.speech.safety import (
    ConfidenceScores,
    PolicyAction,
    RiskLevel,
    VoicePolicyGate,
    VoiceSession,
    classify_risk,
)

# ── Risk classification ────────────────────────────────────────────────


class TestRiskClassification:
    def test_low_risk_endpoints(self):
        assert classify_risk("/system/health") == RiskLevel.LOW
        assert classify_risk("/chat") == RiskLevel.LOW
        assert classify_risk("/help") == RiskLevel.LOW
        assert classify_risk("/flywheel/run") == RiskLevel.LOW

    def test_medium_risk_endpoints(self):
        assert classify_risk("/research/assistant/run") == RiskLevel.MEDIUM
        assert classify_risk("/flywheel/start") == RiskLevel.MEDIUM
        assert classify_risk("/memory/write") == RiskLevel.MEDIUM

    def test_high_risk_endpoints(self):
        assert classify_risk("/governance/execute") == RiskLevel.HIGH

    def test_critical_risk_endpoints(self):
        assert classify_risk("/governance/killswitch/arm") == RiskLevel.CRITICAL
        assert classify_risk("/governance/killswitch/disarm") == RiskLevel.CRITICAL

    def test_unknown_endpoint_defaults_to_medium(self):
        assert classify_risk("/unknown/endpoint") == RiskLevel.MEDIUM

    def test_killswitch_prefix_is_critical(self):
        assert classify_risk("/governance/killswitch/anything") == RiskLevel.CRITICAL


# ── Confidence scoring ─────────────────────────────────────────────────


class TestConfidenceScores:
    def test_zero_policy_denies_all(self):
        c = ConfidenceScores(transcription=0.9, speaker=0.9, intent=0.9, policy=0.0)
        assert c.overall == 0.0

    def test_weighted_average(self):
        c = ConfidenceScores(transcription=1.0, speaker=1.0, intent=1.0, policy=1.0)
        assert abs(c.overall - 1.0) < 0.01

    def test_meets_threshold(self):
        c = ConfidenceScores(transcription=0.9, speaker=0.9, intent=0.9, policy=1.0)
        assert c.meets_threshold(0.6)

    def test_below_threshold(self):
        c = ConfidenceScores(transcription=0.3, speaker=0.3, intent=0.3, policy=0.3)
        assert not c.meets_threshold(0.6)

    def test_as_dict_rounds(self):
        c = ConfidenceScores(transcription=0.123456, speaker=0.0, intent=0.0, policy=0.0)
        d = c.as_dict()
        assert d["transcription"] == 0.123
        assert d["overall"] == 0.0


# ── Voice session ──────────────────────────────────────────────────────


class TestVoiceSession:
    def test_session_has_id(self):
        s = VoiceSession()
        assert len(s.session_id) == 12

    def test_session_has_timestamp(self):
        s = VoiceSession()
        assert "2026" in s.timestamp

    def test_session_has_audit_event_id(self):
        s = VoiceSession()
        assert len(s.audit_event_id) == 32  # uuid hex

    def test_session_serializes(self):
        s = VoiceSession()
        s.transcript_text = "hello"
        s.risk_level = "LOW"
        d = s.as_dict()
        assert d["transcript"]["text"] == "hello"
        assert d["policy"]["risk_level"] == "LOW"
        assert "audit_event_id" in d

    def test_two_sessions_different_ids(self):
        s1 = VoiceSession()
        s2 = VoiceSession()
        assert s1.session_id != s2.session_id


# ── Policy gate ────────────────────────────────────────────────────────


class TestVoicePolicyGate:
    def setup_method(self):
        self.gate = VoicePolicyGate(require_speaker=True)

    def test_low_risk_auto_allows(self):
        decision = self.gate.evaluate(
            "/chat",
            transcript_confidence=0.9,
            speaker_confidence=0.9,
            intent_confidence=0.9,
        )
        assert decision.action == PolicyAction.ALLOW
        assert decision.risk_level == RiskLevel.LOW

    def test_medium_risk_auto_allows(self):
        decision = self.gate.evaluate(
            "/research/assistant/run",
            transcript_confidence=0.9,
            speaker_confidence=0.9,
            intent_confidence=0.9,
        )
        assert decision.action == PolicyAction.ALLOW
        assert decision.risk_level == RiskLevel.MEDIUM

    def test_high_risk_requires_confirmation(self):
        decision = self.gate.evaluate(
            "/governance/execute",
            transcript_confidence=0.9,
            speaker_confidence=0.9,
            intent_confidence=0.9,
        )
        assert decision.action == PolicyAction.CONFIRM
        assert decision.risk_level == RiskLevel.HIGH
        assert decision.requires_confirmation
        assert len(decision.confirmation_token) > 0

    def test_critical_risk_requires_confirmation(self):
        decision = self.gate.evaluate(
            "/governance/killswitch/arm",
            transcript_confidence=0.9,
            speaker_confidence=0.9,
            intent_confidence=0.9,
        )
        assert decision.action == PolicyAction.CONFIRM
        assert decision.risk_level == RiskLevel.CRITICAL

    def test_high_risk_with_confirmation_allows(self):
        decision = self.gate.evaluate(
            "/governance/execute",
            transcript_confidence=0.9,
            speaker_confidence=0.9,
            intent_confidence=0.9,
            confirmed=True,
        )
        assert decision.action == PolicyAction.ALLOW

    def test_critical_risk_with_confirmation_allows(self):
        decision = self.gate.evaluate(
            "/governance/killswitch/arm",
            transcript_confidence=0.9,
            speaker_confidence=0.9,
            intent_confidence=0.9,
            confirmed=True,
        )
        assert decision.action == PolicyAction.ALLOW

    def test_low_transcription_confidence_denies(self):
        decision = self.gate.evaluate(
            "/chat",
            transcript_confidence=0.2,
            speaker_confidence=0.9,
            intent_confidence=0.9,
        )
        assert decision.action == PolicyAction.DENY
        assert any("transcription confidence" in r for r in decision.reasons)

    def test_low_speaker_confidence_denies(self):
        decision = self.gate.evaluate(
            "/chat",
            transcript_confidence=0.9,
            speaker_confidence=0.3,
            intent_confidence=0.9,
        )
        assert decision.action == PolicyAction.DENY
        assert any("speaker confidence" in r for r in decision.reasons)

    def test_low_intent_confidence_denies(self):
        decision = self.gate.evaluate(
            "/chat",
            transcript_confidence=0.9,
            speaker_confidence=0.9,
            intent_confidence=0.2,
        )
        assert decision.action == PolicyAction.DENY
        assert any("intent confidence" in r for r in decision.reasons)

    def test_speaker_not_required_bypasses_speaker_check(self):
        gate = VoicePolicyGate(require_speaker=False)
        decision = gate.evaluate(
            "/chat",
            transcript_confidence=0.9,
            speaker_confidence=0.0,
            intent_confidence=0.9,
        )
        assert decision.action == PolicyAction.ALLOW

    def test_decision_serializes(self):
        decision = self.gate.evaluate(
            "/chat",
            transcript_confidence=0.9,
            speaker_confidence=0.9,
            intent_confidence=0.9,
        )
        d = decision.as_dict()
        assert d["action"] == "ALLOW"
        assert d["risk_level"] == "LOW"
        assert "confidence" in d
        assert "reasons" in d

    def test_fail_closed_on_all_zeros(self):
        decision = self.gate.evaluate(
            "/governance/execute",
            transcript_confidence=0.0,
            speaker_confidence=0.0,
            intent_confidence=0.0,
        )
        assert decision.action == PolicyAction.DENY
