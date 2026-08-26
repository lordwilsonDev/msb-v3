"""Tests for audio capture — file loading only (no microphone)."""

from __future__ import annotations

import struct
import wave
from pathlib import Path

from msb_v3.speech.capture import capture_from_file, save_wav
from msb_v3.speech.models import AudioBuffer


def _create_test_wav(path: Path, duration: float = 1.0, sample_rate: int = 16000) -> None:
    """Create a simple WAV file for testing."""
    n_frames = int(sample_rate * duration)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        # Sine wave at 440Hz
        import math

        frames = bytes()
        for i in range(n_frames):
            value = int(32767 * math.sin(2 * math.pi * 440 * i / sample_rate))
            frames += struct.pack("<h", value)
        wf.writeframes(frames)


def test_load_wav(tmp_path: object) -> None:
    wav_path = Path(str(tmp_path)) / "test.wav"
    _create_test_wav(wav_path, duration=0.5)

    buf = capture_from_file(str(wav_path))
    assert buf.sample_rate == 16000
    assert buf.channels == 1
    assert buf.duration_seconds == pytest.approx(0.5, abs=0.1)
    assert len(buf.samples) > 0


def test_save_and_reload(tmp_path: object) -> None:
    original = AudioBuffer(
        samples=[0.1, -0.1, 0.2, -0.2, 0.0],
        sample_rate=16000,
        duration_seconds=0.0003125,
    )
    wav_path = Path(str(tmp_path)) / "roundtrip.wav"
    save_wav(original, str(wav_path))
    assert wav_path.exists()

    reloaded = capture_from_file(str(wav_path))
    assert reloaded.sample_rate == 16000
    assert len(reloaded.samples) > 0


def test_file_not_found() -> None:
    import pytest

    with pytest.raises(FileNotFoundError):
        capture_from_file("/nonexistent/audio.wav")


def test_save_wav_creates_parent_dirs(tmp_path: object) -> None:
    deep_path = Path(str(tmp_path)) / "a" / "b" / "c" / "out.wav"
    buf = AudioBuffer(samples=[0.0] * 100, sample_rate=16000, duration_seconds=0.00625)
    save_wav(buf, str(deep_path))
    assert deep_path.exists()


# Need pytest for the approx assertion
import pytest  # noqa: E402
