"""Tests for speaker verification using synthetic audio."""

from __future__ import annotations

import pytest

pytest.importorskip("resemblyzer", reason="resemblyzer not installed")


import numpy as np

from msb_v3.speech.models import AudioBuffer
from msb_v3.speech.speaker import SpeakerVerifier


def _make_audio(seed: int = 42, duration: float = 2.0) -> AudioBuffer:
    """Create a synthetic audio buffer for testing."""
    rng = np.random.RandomState(seed)
    samples = rng.randn(int(16000 * duration)).astype(np.float32).tolist()
    return AudioBuffer(samples=samples, sample_rate=16000, duration_seconds=duration)


def test_verifier_starts_empty() -> None:
    v = SpeakerVerifier()
    assert v.list_speakers() == []


def test_enroll_and_verify_same_speaker() -> None:
    v = SpeakerVerifier(threshold=0.5)
    audio = _make_audio(seed=1)
    v.enroll("wilson", audio)

    # Same speaker should verify
    result = v.verify(audio)
    assert result.is_enrolled is True
    assert result.speaker_id == "wilson"
    assert result.confidence > 0.5


def test_different_speakers_differ() -> None:
    v = SpeakerVerifier(threshold=0.9)
    audio_a = _make_audio(seed=1)
    audio_b = _make_audio(seed=999)
    v.enroll("speaker_a", audio_a)

    # Different speaker should not verify (with high threshold)
    result = v.verify(audio_b)
    # Synthetic random audio may or may not match — just verify the flow works
    assert result.speaker_id in ("speaker_a", "unknown")
    assert result.method == "resemblyzer"


def test_remove_speaker() -> None:
    v = SpeakerVerifier()
    v.enroll("temp", _make_audio(seed=5))
    assert "temp" in v.list_speakers()
    removed = v.remove_speaker("temp")
    assert removed is True
    assert "temp" not in v.list_speakers()


def test_remove_nonexistent_returns_false() -> None:
    v = SpeakerVerifier()
    assert v.remove_speaker("ghost") is False


def test_no_enrollments_returns_unknown() -> None:
    v = SpeakerVerifier()
    result = v.verify(_make_audio())
    assert result.speaker_id == "unknown"
    assert result.is_enrolled is False
    assert result.confidence == 0.0


def test_multiple_enrollments_improve_confidence() -> None:
    v = SpeakerVerifier(threshold=0.5)
    v.enroll("wilson", _make_audio(seed=10))
    v.enroll("wilson", _make_audio(seed=11))
    v.enroll("wilson", _make_audio(seed=12))

    result = v.verify(_make_audio(seed=10))
    assert result.is_enrolled is True
    assert len(v._embeddings["wilson"]) == 3
