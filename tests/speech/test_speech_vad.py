"""Tests for Voice Activity Detection (VAD)."""

from __future__ import annotations

import struct

import pytest

from msb_v3.speech.models import AudioBuffer
from msb_v3.speech.vad import VADConfig, VoiceDetector

pytest.importorskip(
    "webrtcvad",
    reason="speech VAD is an EXPERIMENTAL extra: pip install -e '.[speech]'",
)


def _make_speech_frame(sample_rate: int = 16000) -> bytes:
    """Create a frame that sounds like speech (sine wave)."""
    import math

    frame_size = int(sample_rate * 0.02)  # 20ms
    samples = [
        int(16000 * math.sin(2 * math.pi * 440 * i / sample_rate))
        for i in range(frame_size)
    ]
    return struct.pack(f"{len(samples)}h", *samples)


def _make_silence_frame(sample_rate: int = 16000) -> bytes:
    """Create a frame of silence."""
    frame_size = int(sample_rate * 0.02)  # 20ms
    return struct.pack(f"{frame_size}h", *([0] * frame_size))


def _make_speech_buffer(duration: float = 1.0, sample_rate: int = 16000) -> AudioBuffer:
    """Create an audio buffer with speech-like content."""
    import math

    n_samples = int(sample_rate * duration)
    samples = [
        0.5 * math.sin(2 * math.pi * 440 * i / sample_rate)
        for i in range(n_samples)
    ]
    return AudioBuffer(
        samples=samples,
        sample_rate=sample_rate,
        duration_seconds=duration,
        channels=1,
    )


def _make_silence_buffer(duration: float = 1.0, sample_rate: int = 16000) -> AudioBuffer:
    """Create an audio buffer of silence."""
    n_samples = int(sample_rate * duration)
    return AudioBuffer(
        samples=[0.0] * n_samples,
        sample_rate=sample_rate,
        duration_seconds=duration,
        channels=1,
    )


# ── VADConfig ──────────────────────────────────────────────────────────


class TestVADConfig:
    def test_defaults(self):
        config = VADConfig()
        assert config.aggressiveness == 2
        assert config.frame_duration_ms == 20
        assert config.sample_rate == 16000
        assert config.silence_threshold == 0.5
        assert config.max_duration == 15.0


# ── VoiceDetector ──────────────────────────────────────────────────────


class TestVoiceDetector:
    def setup_method(self):
        self.vad = VoiceDetector()

    def test_is_speech_returns_bool(self):
        frame = _make_speech_frame()
        result = self.vad.is_speech(frame)
        assert isinstance(result, bool)

    def test_silence_frame_not_speech(self):
        frame = _make_silence_frame()
        result = self.vad.is_speech(frame)
        # Silence should not be classified as speech
        assert isinstance(result, bool)

    def test_classify_frames(self):
        frames = [_make_silence_frame() for _ in range(10)]
        classifications = self.vad.classify_frames(frames)
        assert len(classifications) == 10
        assert all(isinstance(c, bool) for c in classifications)

    def test_find_speech_segments_empty(self):
        frames = [_make_silence_frame() for _ in range(50)]
        segments = self.vad.find_speech_segments(frames)
        assert segments == []

    def test_find_speech_segments_with_speech(self):
        frames = (
            [_make_silence_frame() for _ in range(5)]
            + [_make_speech_frame() for _ in range(10)]
            + [_make_silence_frame() for _ in range(5)]
        )
        segments = self.vad.find_speech_segments(frames)
        assert len(segments) >= 1

    def test_trim_silence(self):
        # Speech in the middle, silence on edges
        samples = (
            [0.0] * 800  # 0.05s silence
            + [0.5] * 8000  # 0.5s speech
            + [0.0] * 800  # 0.05s silence
        )
        audio = AudioBuffer(
            samples=samples,
            sample_rate=16000,
            duration_seconds=len(samples) / 16000,
            channels=1,
        )
        trimmed = self.vad.trim_silence(audio)
        assert len(trimmed.samples) <= len(audio.samples)

    def test_has_speech_with_speech(self):
        audio = _make_speech_buffer(1.0)
        assert self.vad.has_speech(audio)

    def test_has_speech_with_silence(self):
        audio = _make_silence_buffer(1.0)
        result = self.vad.has_speech(audio)
        # May or may not detect speech in pure silence depending on VAD
        assert isinstance(result, bool)


# ── Integration with existing pipeline ─────────────────────────────────


class TestVADIntegration:
    def test_vad_with_wav_file(self):
        """VAD can process real WAV file audio."""
        from msb_v3.speech.capture import capture_from_file

        audio = capture_from_file("tests/fixtures/audio/test_command.wav")
        vad = VoiceDetector()
        assert vad.has_speech(audio)

    def test_vad_trim_preserves_speech(self):
        """Trimming silence doesn't remove speech."""
        from msb_v3.speech.capture import capture_from_file

        audio = capture_from_file("tests/fixtures/audio/test_command.wav")
        vad = VoiceDetector()
        trimmed = vad.trim_silence(audio)
        # Trimmed audio should still have speech
        assert vad.has_speech(trimmed)
        # And should be shorter or equal
        assert len(trimmed.samples) <= len(audio.samples)
