"""Tests for speech pipeline data models."""

from __future__ import annotations

from msb_v3.speech.models import (
    AudioBuffer,
    PipelineResult,
    SpeakerIdentity,
    Transcript,
    VoiceCommand,
)


def test_audio_buffer_defaults() -> None:
    buf = AudioBuffer()
    assert buf.samples == []
    assert buf.sample_rate == 16000
    assert buf.duration_seconds == 0.0
    assert buf.channels == 1


def test_transcript_defaults() -> None:
    t = Transcript()
    assert t.text == ""
    assert t.language == "en"
    assert t.engine == "unknown"


def test_speaker_identity_defaults() -> None:
    s = SpeakerIdentity()
    assert s.speaker_id == "unknown"
    assert s.confidence == 0.0
    assert s.is_enrolled is False


def test_voice_command_defaults() -> None:
    c = VoiceCommand()
    assert c.command == ""
    assert c.endpoint == ""
    assert c.method == "POST"


def test_pipeline_result_to_dict_minimal() -> None:
    r = PipelineResult(authorized=False, authorization_reason="denied")
    d = r.to_dict()
    assert d["authorized"] is False
    assert d["authorization_reason"] == "denied"
    assert "transcript" not in d
    assert "speaker" not in d


def test_pipeline_result_to_dict_full() -> None:
    r = PipelineResult(
        transcript=Transcript(text="hello", confidence=0.9),
        speaker=SpeakerIdentity(speaker_id="wilson", confidence=0.85, is_enrolled=True),
        command=VoiceCommand(command="chat", endpoint="/chat", method="POST"),
        authorized=True,
        authorization_reason="verified",
    )
    d = r.to_dict()
    assert d["authorized"] is True
    assert d["transcript"]["text"] == "hello"
    assert d["speaker"]["speaker_id"] == "wilson"
    assert d["command"]["endpoint"] == "/chat"


def test_pipeline_result_to_dict_with_error() -> None:
    r = PipelineResult(error="capture failed", authorized=False)
    d = r.to_dict()
    assert d["error"] == "capture failed"
