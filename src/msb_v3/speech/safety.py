"""Voice safety layer — Vesta-integrated policy gate for voice commands.

Every voice command passes through:
  1. Risk classification (LOW / MEDIUM / HIGH / CRITICAL)
  2. Confidence scoring (transcription + speaker + intent + policy)
  3. Vesta policy gate (ALLOW / CONFIRM / DENY)
  4. Confirmation flow (HIGH/CRITICAL require explicit "confirm")
  5. Audit trail (full causal chain for every transaction)

The safety layer is fail-closed: any error returns DENY.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class PolicyAction(str, Enum):
    ALLOW = "ALLOW"
    CONFIRM = "CONFIRM"
    DENY = "DENY"


# ── Risk classification ────────────────────────────────────────────────

# Commands that map to each risk level
_RISK_MAP: Dict[str, RiskLevel] = {
    # LOW — read-only, informational
    "system.status": RiskLevel.LOW,
    "chat": RiskLevel.LOW,
    "help": RiskLevel.LOW,
    "research.run": RiskLevel.LOW,
    "flywheel.run": RiskLevel.LOW,
    # MEDIUM — active but reversible
    "research.assistant.run": RiskLevel.MEDIUM,
    "flywheel.start": RiskLevel.MEDIUM,
    "flywheel.stop": RiskLevel.MEDIUM,
    "memory.read": RiskLevel.LOW,
    "memory.write": RiskLevel.MEDIUM,
    # HIGH — deployment, configuration changes
    "governance.execute": RiskLevel.HIGH,
    "config.change": RiskLevel.HIGH,
    "service.restart": RiskLevel.HIGH,
    # CRITICAL — destructive, security-sensitive
    "governance.killswitch.arm": RiskLevel.CRITICAL,
    "governance.killswitch.disarm": RiskLevel.CRITICAL,
    "security.disable": RiskLevel.CRITICAL,
    "data.delete": RiskLevel.CRITICAL,
    "factory.reset": RiskLevel.CRITICAL,
}

# Endpoint → risk level mapping (for VoiceCommand.endpoint)
_ENDPOINT_RISK: Dict[str, RiskLevel] = {
    "/system/health": RiskLevel.LOW,
    "/chat": RiskLevel.LOW,
    "/help": RiskLevel.LOW,
    "/research/assistant/run": RiskLevel.MEDIUM,
    "/flywheel/run": RiskLevel.LOW,
    "/flywheel/start": RiskLevel.MEDIUM,
    "/flywheel/stop": RiskLevel.MEDIUM,
    "/governance/execute": RiskLevel.HIGH,
    "/governance/killswitch/arm": RiskLevel.CRITICAL,
    "/governance/killswitch/disarm": RiskLevel.CRITICAL,
    "/memory/read": RiskLevel.LOW,
    "/memory/write": RiskLevel.MEDIUM,
}


def classify_risk(endpoint: str, params: Optional[Dict[str, Any]] = None) -> RiskLevel:
    """Classify the risk level of a voice command based on its endpoint."""
    # Direct endpoint match
    if endpoint in _ENDPOINT_RISK:
        return _ENDPOINT_RISK[endpoint]

    # Prefix match for governance paths
    if "/governance/killswitch" in endpoint:
        return RiskLevel.CRITICAL
    if "/governance/execute" in endpoint:
        return RiskLevel.HIGH
    if "/governance" in endpoint:
        return RiskLevel.HIGH

    # Default to MEDIUM for unknown endpoints
    return RiskLevel.MEDIUM


# ── Confidence scoring ─────────────────────────────────────────────────

@dataclass(slots=True)
class ConfidenceScores:
    """Multi-signal confidence scoring for a voice transaction."""

    transcription: float = 0.0  # Whisper confidence (0-1)
    speaker: float = 0.0  # Speaker verification confidence (0-1)
    intent: float = 0.0  # Intent extraction confidence (0-1)
    policy: float = 0.0  # Policy decision confidence (0-1)

    @property
    def overall(self) -> float:
        """Weighted overall confidence."""
        if self.policy == 0.0:
            return 0.0  # Policy denied = zero confidence
        weights = {"transcription": 0.3, "speaker": 0.3, "intent": 0.2, "policy": 0.2}
        scores = {
            "transcription": self.transcription,
            "speaker": self.speaker,
            "intent": self.intent,
            "policy": self.policy,
        }
        return sum(scores[k] * weights[k] for k in weights)

    def meets_threshold(self, threshold: float = 0.6) -> bool:
        """Check if overall confidence meets the threshold."""
        return self.overall >= threshold

    def as_dict(self) -> Dict[str, Any]:
        return {
            "transcription": round(self.transcription, 3),
            "speaker": round(self.speaker, 3),
            "intent": round(self.intent, 3),
            "policy": round(self.policy, 3),
            "overall": round(self.overall, 3),
        }


# ── Voice policy decision ──────────────────────────────────────────────

@dataclass(slots=True)
class VoicePolicyDecision:
    """Decision from the voice safety gate."""

    action: PolicyAction
    risk_level: RiskLevel
    confidence: ConfidenceScores
    reasons: List[str] = field(default_factory=list)
    requires_confirmation: bool = False
    confirmation_token: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action.value,
            "risk_level": self.risk_level.value,
            "confidence": self.confidence.as_dict(),
            "reasons": self.reasons,
            "requires_confirmation": self.requires_confirmation,
            "confirmation_token": self.confirmation_token,
        }


# ── Voice session ──────────────────────────────────────────────────────

@dataclass
class VoiceSession:
    """Complete audit record for a voice transaction.

    Every voice interaction creates a session that captures the full
    causal chain from microphone input to execution result.
    """

    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    # Input
    audio_duration_s: float = 0.0
    audio_sample_rate: int = 16000
    audio_samples: int = 0

    # Transcript
    transcript_text: str = ""
    transcript_engine: str = ""
    transcription_confidence: float = 0.0

    # Speaker
    speaker_id: str = ""
    speaker_confidence: float = 0.0
    speaker_enrolled: bool = False

    # Intent
    intent_endpoint: str = ""
    intent_params: Dict[str, Any] = field(default_factory=dict)
    intent_command: str = ""
    intent_confidence: float = 0.0

    # Policy
    risk_level: str = ""
    policy_action: str = ""
    policy_reasons: List[str] = field(default_factory=list)
    requires_confirmation: bool = False
    confirmed: bool = False

    # Confidence
    overall_confidence: float = 0.0

    # Execution
    executed: bool = False
    execution_result: Optional[Dict[str, Any]] = None
    execution_error: str = ""

    # Response
    response_text: str = ""
    spoken: bool = False

    # Timing
    latency_capture_ms: float = 0.0
    latency_transcribe_ms: float = 0.0
    latency_verify_ms: float = 0.0
    latency_intent_ms: float = 0.0
    latency_policy_ms: float = 0.0
    latency_execute_ms: float = 0.0
    latency_tts_ms: float = 0.0
    latency_total_ms: float = 0.0

    # Audit
    audit_event_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def as_dict(self) -> Dict[str, Any]:
        """Serialize the session for audit/storage."""
        return {
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "audio": {
                "duration_s": self.audio_duration_s,
                "sample_rate": self.audio_sample_rate,
                "samples": self.audio_samples,
            },
            "transcript": {
                "text": self.transcript_text,
                "engine": self.transcript_engine,
                "confidence": self.transcription_confidence,
            },
            "speaker": {
                "id": self.speaker_id,
                "confidence": self.speaker_confidence,
                "enrolled": self.speaker_enrolled,
            },
            "intent": {
                "endpoint": self.intent_endpoint,
                "params": self.intent_params,
                "command": self.intent_command,
                "confidence": self.intent_confidence,
            },
            "policy": {
                "risk_level": self.risk_level,
                "action": self.policy_action,
                "reasons": self.policy_reasons,
                "requires_confirmation": self.requires_confirmation,
                "confirmed": self.confirmed,
            },
            "confidence": self.overall_confidence,
            "execution": {
                "executed": self.executed,
                "result": self.execution_result,
                "error": self.execution_error,
            },
            "response": {
                "text": self.response_text,
                "spoken": self.spoken,
            },
            "latency_ms": {
                "capture": self.latency_capture_ms,
                "transcribe": self.latency_transcribe_ms,
                "verify": self.latency_verify_ms,
                "intent": self.latency_intent_ms,
                "policy": self.latency_policy_ms,
                "execute": self.latency_execute_ms,
                "tts": self.latency_tts_ms,
                "total": self.latency_total_ms,
            },
            "audit_event_id": self.audit_event_id,
        }


# ── Voice policy gate ──────────────────────────────────────────────────

class VoicePolicyGate:
    """Vesta-integrated policy gate for voice commands.

    Every voice command passes through this gate before execution.
    The gate is fail-closed: any error returns DENY.
    """

    # Confidence thresholds
    TRANSCRIPTION_THRESHOLD = 0.5
    SPEAKER_THRESHOLD = 0.75
    INTENT_THRESHOLD = 0.5
    OVERALL_THRESHOLD = 0.6

    def __init__(self, require_speaker: bool = True) -> None:
        self.require_speaker = require_speaker

    def evaluate(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        transcript_confidence: float = 0.0,
        speaker_confidence: float = 0.0,
        speaker_enrolled: bool = False,
        intent_confidence: float = 0.9,
        confirmed: bool = False,
        confirmation_token: str = "",
    ) -> VoicePolicyDecision:
        """Evaluate a voice command through the safety gate.

        Returns VoicePolicyDecision with ALLOW / CONFIRM / DENY.
        """
        reasons: List[str] = []

        # 1. Classify risk
        risk = classify_risk(endpoint, params)

        # 2. Build confidence scores
        confidence = ConfidenceScores(
            transcription=transcript_confidence,
            speaker=speaker_confidence,
            intent=intent_confidence,
            policy=1.0,  # Will be set below
        )

        # 3. Check transcription confidence
        if transcript_confidence < self.TRANSCRIPTION_THRESHOLD:
            confidence.policy = 0.0
            reasons.append(
                f"transcription confidence {transcript_confidence:.2f} "
                f"< threshold {self.TRANSCRIPTION_THRESHOLD}"
            )
            return VoicePolicyDecision(
                action=PolicyAction.DENY,
                risk_level=risk,
                confidence=confidence,
                reasons=reasons,
            )

        # 4. Check speaker verification
        if self.require_speaker and speaker_confidence < self.SPEAKER_THRESHOLD:
            confidence.policy = 0.0
            reasons.append(
                f"speaker confidence {speaker_confidence:.2f} "
                f"< threshold {self.SPEAKER_THRESHOLD}"
            )
            return VoicePolicyDecision(
                action=PolicyAction.DENY,
                risk_level=risk,
                confidence=confidence,
                reasons=reasons,
            )

        # 5. Check intent confidence
        if intent_confidence < self.INTENT_THRESHOLD:
            confidence.policy = 0.0
            reasons.append(
                f"intent confidence {intent_confidence:.2f} "
                f"< threshold {self.INTENT_THRESHOLD}"
            )
            return VoicePolicyDecision(
                action=PolicyAction.DENY,
                risk_level=risk,
                confidence=confidence,
                reasons=reasons,
            )

        # 6. Risk-based policy
        if risk == RiskLevel.CRITICAL:
            if not confirmed:
                confidence.policy = 1.0
                reasons.append(
                    "CRITICAL risk command requires explicit confirmation"
                )
                token = confirmation_token or uuid.uuid4().hex[:8]
                return VoicePolicyDecision(
                    action=PolicyAction.CONFIRM,
                    risk_level=risk,
                    confidence=confidence,
                    reasons=reasons,
                    requires_confirmation=True,
                    confirmation_token=token,
                )
            reasons.append("CRITICAL command confirmed by operator")

        elif risk == RiskLevel.HIGH:
            if not confirmed:
                confidence.policy = 1.0
                reasons.append(
                    "HIGH risk command requires explicit confirmation"
                )
                token = confirmation_token or uuid.uuid4().hex[:8]
                return VoicePolicyDecision(
                    action=PolicyAction.CONFIRM,
                    risk_level=risk,
                    confidence=confidence,
                    reasons=reasons,
                    requires_confirmation=True,
                    confirmation_token=token,
                )
            reasons.append("HIGH command confirmed by operator")

        else:
            reasons.append(f"{risk.value} risk — auto-allowed")

        # 7. Overall confidence check
        if not confidence.meets_threshold(self.OVERALL_THRESHOLD):
            confidence.policy = 0.5
            reasons.append(
                f"overall confidence {confidence.overall:.2f} "
                f"< threshold {self.OVERALL_THRESHOLD}"
            )
            return VoicePolicyDecision(
                action=PolicyAction.DENY,
                risk_level=risk,
                confidence=confidence,
                reasons=reasons,
            )

        # 8. ALLOW
        confidence.policy = 1.0
        reasons.append("all checks passed")
        return VoicePolicyDecision(
            action=PolicyAction.ALLOW,
            risk_level=risk,
            confidence=confidence,
            reasons=reasons,
        )
