from __future__ import annotations

import numpy as np

from msb_v3.speech.realtime.audio import (
    MIC_RATE,
    pcm16_seconds,
    pcm16_to_float,
    resample_pcm16,
)


def _tone(seconds: float, rate: int) -> bytes:
    t = np.arange(int(seconds * rate)) / rate
    return (0.5 * np.sin(2 * np.pi * 440 * t) * 32767).astype("<i2").tobytes()


def test_seconds():
    assert pcm16_seconds(_tone(1.5, MIC_RATE), MIC_RATE) == 1.5


def test_seconds_empty():
    assert pcm16_seconds(b"", MIC_RATE) == 0.0


def test_to_float_range_and_length():
    data = _tone(0.1, MIC_RATE)
    floats = pcm16_to_float(data)
    assert len(floats) == len(data) // 2
    assert max(floats) <= 1.0 and min(floats) >= -1.0
    assert max(floats) > 0.45


def test_resample_16k_to_24k_length():
    out = resample_pcm16(_tone(1.0, 16000), 16000, 24000)
    assert len(out) // 2 == 24000


def test_resample_preserves_amplitude():
    out = np.frombuffer(resample_pcm16(_tone(0.5, 16000), 16000, 24000), dtype="<i2")
    assert 0.45 * 32767 < np.abs(out).max() <= 32767


def test_resample_same_rate_is_identity():
    data = _tone(0.2, 16000)
    assert resample_pcm16(data, 16000, 16000) == data


def test_resample_empty():
    assert resample_pcm16(b"", 16000, 24000) == b""
