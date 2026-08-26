"""Tests for the full speech pipeline orchestrator."""

from __future__ import annotations

from unittest.mock import MagicMock

from msb_v3.speech.models import (
    AudioBuffer,
    SpeakerIdentity,
    Transcript,
)
from msb_v3.speech.pipeline import SpeechPipeline


def _mock_transcript(text: str = "research AI inference") -> Transcript:
    return Transcript(text=text, language="en", confidence=0.9, engine="test")


def _mock_speaker(speaker_id: str = "wilson", enrolled: bool = True) -> SpeakerIdentity:
    return SpeakerIdentity(
        speaker_id=speaker_id, confidence=0.85, is_enrolled=enrolled
    )


def _mock_audio() -> AudioBuffer:
    return AudioBuffer(samples=[0.0] * 16000, sample_rate=16000, duration_seconds=1.0)


def test_pipeline_demo_mode_no_speakers() -> None:
    """With no enrolled speakers, pipeline runs in demo mode (authorized)."""
    pipeline = SpeechPipeline()
    # Mock the internal components to avoid real model loading
    pipeline._transcribe = MagicMock(return_value=_mock_transcript())
    pipeline._verify_speaker = MagicMock(return_value=_mock_speaker(enrolled=False))
    # Override list_speakers to return empty
    pipeline.verifier.list_speakers = MagicMock(return_value=[])

    result = pipeline.process_audio(_mock_audio())
    assert result.authorized is True
    assert "demo mode" in result.authorization_reason


def test_pipeline_verified_speaker() -> None:
    """Enrolled speaker gets authorized."""
    pipeline = SpeechPipeline()
    pipeline._transcribe = MagicMock(return_value=_mock_transcript())
    pipeline._verify_speaker = MagicMock(
        return_value=_mock_speaker(speaker_id="wilson", enrolled=True)
    )
    pipeline.verifier.list_speakers = MagicMock(return_value=["wilson"])

    result = pipeline.process_audio(_mock_audio())
    assert result.authorized is True
    assert "wilson" in result.authorization_reason
    assert result.command is not None
    assert result.command.endpoint == "/research/assistant/run"


def test_pipeline_unknown_speaker_denied() -> None:
    """Unknown speaker is denied (fail-closed)."""
    pipeline = SpeechPipeline()
    pipeline._transcribe = MagicMock(return_value=_mock_transcript())
    pipeline._verify_speaker = MagicMock(
        return_value=_mock_speaker(speaker_id="unknown", enrolled=False)
    )
    pipeline.verifier.list_speakers = MagicMock(return_value=["wilson"])

    result = pipeline.process_audio(_mock_audio())
    assert result.authorized is False
    assert "unknown speaker" in result.authorization_reason.lower()


def test_pipeline_empty_transcript() -> None:
    """Empty transcription fails the pipeline."""
    pipeline = SpeechPipeline()
    pipeline._transcribe = MagicMock(return_value=Transcript(text="", engine="test"))
    pipeline._verify_speaker = MagicMock(return_value=_mock_speaker())

    result = pipeline.process_audio(_mock_audio())
    assert result.authorized is False
    assert "empty" in result.error.lower()


def test_pipeline_transcription_failure() -> None:
    """Transcription failure fails the pipeline."""
    pipeline = SpeechPipeline()
    pipeline._transcribe = MagicMock(return_value=None)

    result = pipeline.process_audio(_mock_audio())
    assert result.authorized is False
    assert "transcription" in result.error.lower()


def test_pipeline_expected_speaker_match() -> None:
    """When a specific speaker_id is expected, only that speaker is authorized."""
    pipeline = SpeechPipeline()
    pipeline._transcribe = MagicMock(return_value=_mock_transcript())
    pipeline._verify_speaker = MagicMock(
        return_value=_mock_speaker(speaker_id="wilson", enrolled=True)
    )
    pipeline.verifier.list_speakers = MagicMock(return_value=["wilson"])

    result = pipeline.process_audio(_mock_audio(), speaker_id="wilson")
    assert result.authorized is True


def test_pipeline_expected_speaker_mismatch() -> None:
    """When a specific speaker_id is expected, wrong speaker is denied."""
    pipeline = SpeechPipeline()
    pipeline._transcribe = MagicMock(return_value=_mock_transcript())
    pipeline._verify_speaker = MagicMock(
        return_value=_mock_speaker(speaker_id="other", enrolled=True)
    )
    pipeline.verifier.list_speakers = MagicMock(return_value=["wilson", "other"])

    result = pipeline.process_audio(_mock_audio(), speaker_id="wilson")
    assert result.authorized is False
    assert "expected wilson" in result.authorization_reason


def test_pipeline_result_has_timestamp() -> None:
    """Pipeline result always has a timestamp."""
    pipeline = SpeechPipeline()
    pipeline._transcribe = MagicMock(return_value=_mock_transcript())
    pipeline._verify_speaker = MagicMock(return_value=_mock_speaker())
    pipeline.verifier.list_speakers = MagicMock(return_value=[])

    result = pipeline.process_audio(_mock_audio())
    assert result.timestamp != ""


def test_pipeline_enroll_speaker() -> None:
    """Enrolling a speaker adds to the verifier."""
    pipeline = SpeechPipeline()
    pipeline.verifier.enroll = MagicMock()

    outcome = pipeline.enroll_speaker("new_user", _mock_audio())
    assert outcome["enrolled"] is True
    assert outcome["speaker_id"] == "new_user"
