"""PCM16 helpers for the realtime path (mono, little-endian)."""

from __future__ import annotations

import numpy as np

MIC_RATE = 16000
PROVIDER_OUT_RATE = 24000


def pcm16_seconds(data: bytes, rate: int) -> float:
    """Duration of mono PCM16 ``data`` at ``rate``."""
    return (len(data) // 2) / rate


def pcm16_to_float(data: bytes) -> list[float]:
    """PCM16 bytes → floats in [-1, 1] (the ``AudioBuffer.samples`` shape)."""
    arr = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
    return arr.tolist()


def resample_pcm16(data: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Linear-interpolation resample. Good enough for speech to a speech API."""
    if src_rate == dst_rate or not data:
        return data
    src = np.frombuffer(data, dtype="<i2").astype(np.float32)
    n_out = int(round(len(src) * dst_rate / src_rate))
    x_old = np.arange(len(src))
    x_new = np.linspace(0, len(src) - 1, n_out)
    out = np.interp(x_new, x_old, src)
    return np.clip(out, -32768, 32767).astype("<i2").tobytes()
