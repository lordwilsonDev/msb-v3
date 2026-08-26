"""Shared data models for the speech pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class AudioBuffer:
    """Raw audio captured from microphone."""

    samples: list[float] = field(default_factory=list)
    sample_rate: int = 16000
    duration_seconds: float = 0.0
    channels: int = 1
    format: str = "float32"


@dataclass
class Transcript:
    """Result of speech-to-text transcription."""

    text: str = ""
    language: str = "en"
    confidence: float = 0.0
    duration_seconds: float = 0.0
    segments: list[Dict[str, Any]] = field(default_factory=list)
    engine: str = "unknown"


@dataclass
class SpeakerIdentity:
    """Result of speaker verification."""

    speaker_id: str = "unknown"
    confidence: float = 0.0
    is_enrolled: bool = False
    embedding: list[float] = field(default_factory=list)
    method: str = "resemblyzer"


@dataclass
class VoiceCommand:
    """Extracted intent from speech."""

    command: str = ""
    endpoint: str = ""
    method: str = "POST"
    params: Dict[str, Any] = field(default_factory=dict)
    raw_transcript: str = ""
    confidence: float = 0.0


@dataclass
class PipelineResult:
    """Full pipeline output — one object containing everything."""

    audio: Optional[AudioBuffer] = None
    transcript: Optional[Transcript] = None
    speaker: Optional[SpeakerIdentity] = None
    command: Optional[VoiceCommand] = None
    authorized: bool = False
    authorization_reason: str = ""
    error: str = ""
    timestamp: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for API responses and audit trails."""
        result: Dict[str, Any] = {
            "authorized": self.authorized,
            "authorization_reason": self.authorization_reason,
            "timestamp": self.timestamp,
        }
        if self.transcript:
            result["transcript"] = {
                "text": self.transcript.text,
                "language": self.transcript.language,
                "confidence": self.transcript.confidence,
                "engine": self.transcript.engine,
            }
        if self.speaker:
            result["speaker"] = {
                "speaker_id": self.speaker.speaker_id,
                "confidence": self.speaker.confidence,
                "is_enrolled": self.speaker.is_enrolled,
            }
        if self.command:
            result["command"] = {
                "command": self.command.command,
                "endpoint": self.command.endpoint,
                "method": self.command.method,
                "params": self.command.params,
            }
        if self.error:
            result["error"] = self.error
        return result
