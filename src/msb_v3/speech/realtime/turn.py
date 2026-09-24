"""Local end-of-turn detection: the turn ends after speech followed by silence.

Runs on the Mac, on the already-captured audio — the provider never decides
where a turn ends. Leading silence never ends a turn (you may not have
started talking yet); ``max_seconds`` caps a turn that never goes quiet.
"""

from __future__ import annotations

from typing import Callable

from msb_v3.speech.realtime.audio import MIC_RATE, pcm16_seconds


class EndOfTurnDetector:
    def __init__(
        self,
        is_speech: Callable[[bytes], bool],
        *,
        silence_after: float = 1.0,
        max_seconds: float = 30.0,
        rate: int = MIC_RATE,
    ) -> None:
        self.is_speech = is_speech
        self.silence_after = silence_after
        self.max_seconds = max_seconds
        self.rate = rate
        self.speech_seconds = 0.0
        self._silence = 0.0
        self._total = 0.0

    def update(self, chunk: bytes) -> bool:
        """Feed one chunk; return True when the turn should end."""
        seconds = pcm16_seconds(chunk, self.rate)
        self._total += seconds
        if self.is_speech(chunk):
            self.speech_seconds += seconds
            self._silence = 0.0
        elif self.speech_seconds > 0:
            self._silence += seconds
        if self._total >= self.max_seconds - 1e-9:
            return True
        return self.speech_seconds > 0 and self._silence >= self.silence_after - 1e-9


def webrtc_is_speech(aggressiveness: int = 2, rate: int = MIC_RATE) -> Callable[[bytes], bool]:
    """Chunk-level speech check: speech if ≥30% of its 20 ms frames are speech."""
    import webrtcvad

    vad = webrtcvad.Vad(aggressiveness)
    frame_bytes = int(rate * 0.02) * 2

    def check(chunk: bytes) -> bool:
        frames = [chunk[i:i + frame_bytes] for i in range(0, len(chunk) - frame_bytes + 1,
                                                          frame_bytes)]
        if not frames:
            return False
        hits = sum(vad.is_speech(f, rate) for f in frames)
        return hits / len(frames) >= 0.3

    return check
