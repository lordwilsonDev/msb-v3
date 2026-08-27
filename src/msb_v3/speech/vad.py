"""Voice Activity Detection (VAD) — detect speech start/end.

Uses webrtcvad to classify audio frames as speech or silence.
Enables variable-length capture: record until speech ends, not for
a fixed duration.

Architecture:
    PyAudio stream → 20ms frames → webrtcvad → speech/silence → buffer

Usage::

    from msb_v3.speech.vad import VoiceDetector

    vad = VoiceDetector()
    audio = vad.capture_until_silence(duration_seconds=10, silence_after=1.0)
    # audio contains only the speech portion
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import List, Optional

from msb_v3.speech.models import AudioBuffer


@dataclass
class VADConfig:
    """VAD configuration."""

    aggressiveness: int = 2  # 0-3, higher = more aggressive filtering
    frame_duration_ms: int = 20  # 10, 20, or 30ms frames
    sample_rate: int = 16000
    silence_threshold: float = 0.5  # seconds of silence before stopping
    max_duration: float = 15.0  # maximum recording duration
    min_speech_frames: int = 3  # minimum frames to count as speech


class VoiceDetector:
    """Voice Activity Detection for variable-length capture.

    Detects when speech starts and ends, allowing the system to:
    - Start recording only when speech is detected
    - Stop recording after a configurable silence period
    - Avoid wasting time on silence
    """

    def __init__(self, config: Optional[VADConfig] = None) -> None:
        self.config = config or VADConfig()
        import webrtcvad

        self._vad = webrtcvad.Vad(self.config.aggressiveness)
        self._frame_size = int(
            self.config.sample_rate * self.config.frame_duration_ms / 1000
        )

    def is_speech(self, frame: bytes) -> bool:
        """Classify a single frame as speech or silence."""
        return self._vad.is_speech(frame, self.config.sample_rate)

    def classify_frames(self, frames: List[bytes]) -> List[bool]:
        """Classify a list of frames as speech (True) or silence (False)."""
        return [self.is_speech(f) for f in frames]

    def find_speech_segments(
        self, frames: List[bytes]
    ) -> List[tuple[int, int]]:
        """Find contiguous speech segments in a list of frames.

        Returns list of (start_frame, end_frame) tuples.
        """
        classifications = self.classify_frames(frames)
        segments: List[tuple[int, int]] = []
        in_speech = False
        start = 0

        for i, is_speech in enumerate(classifications):
            if is_speech and not in_speech:
                start = i
                in_speech = True
            elif not is_speech and in_speech:
                if i - start >= self.config.min_speech_frames:
                    segments.append((start, i))
                in_speech = False

        # Handle trailing speech
        if in_speech and len(frames) - start >= self.config.min_speech_frames:
            segments.append((start, len(frames)))

        return segments

    def trim_silence(self, audio: AudioBuffer) -> AudioBuffer:
        """Trim leading and trailing silence from an audio buffer."""
        frame_bytes = self._frame_size * 2  # 16-bit = 2 bytes per sample
        int16_samples = [max(-32768, min(32767, int(s * 32767))) for s in audio.samples]
        all_bytes = struct.pack(f"{len(int16_samples)}h", *int16_samples)

        frames = [
            all_bytes[i : i + frame_bytes]
            for i in range(0, len(all_bytes) - frame_bytes + 1, frame_bytes)
        ]

        if not frames:
            return audio

        classifications = self.classify_frames(frames)

        # Find first speech frame
        first_speech = 0
        for i, is_speech in enumerate(classifications):
            if is_speech:
                first_speech = i
                break

        # Find last speech frame
        last_speech = len(classifications) - 1
        for i in range(len(classifications) - 1, -1, -1):
            if classifications[i]:
                last_speech = i
                break

        # Convert frame indices to sample indices
        samples_per_frame = self._frame_size
        start_sample = first_speech * samples_per_frame
        end_sample = min((last_speech + 1) * samples_per_frame, len(audio.samples))

        trimmed = audio.samples[start_sample:end_sample]
        duration = len(trimmed) / audio.sample_rate

        return AudioBuffer(
            samples=trimmed,
            sample_rate=audio.sample_rate,
            duration_seconds=duration,
            channels=audio.channels,
        )

    def has_speech(self, audio: AudioBuffer, threshold: float = 0.1) -> bool:
        """Check if an audio buffer contains any speech."""
        frame_bytes = self._frame_size * 2
        int16_samples = [max(-32768, min(32767, int(s * 32767))) for s in audio.samples]
        all_bytes = struct.pack(f"{len(int16_samples)}h", *int16_samples)

        frames = [
            all_bytes[i : i + frame_bytes]
            for i in range(0, len(all_bytes) - frame_bytes + 1, frame_bytes)
        ]

        if not frames:
            return False

        speech_count = sum(1 for f in frames if self.is_speech(f))
        return (speech_count / len(frames)) >= threshold


def capture_until_silence(
    duration_seconds: float = 15.0,
    silence_after: float = 1.0,
    sample_rate: int = 16000,
    max_duration: float = 15.0,
) -> AudioBuffer:
    """Capture audio from microphone until silence is detected.

    Records continuously, stopping after `silence_after` seconds of
    silence following speech. Returns the captured audio trimmed to
    the speech portion.

    This replaces the fixed-duration capture model with intelligent
    voice activity detection.
    """
    try:
        import pyaudio
    except ImportError as exc:
        raise RuntimeError("PyAudio not installed — pip install pyaudio") from exc

    vad = VoiceDetector(
        VADConfig(
            sample_rate=sample_rate,
            silence_threshold=silence_after,
            max_duration=max_duration,
        )
    )

    pa = pyaudio.PyAudio()
    try:
        stream = pa.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=sample_rate,
            input=True,
            frames_per_buffer=vad._frame_size,
        )
    except OSError as exc:
        raise RuntimeError(f"Cannot open microphone: {exc}") from exc

    frames: List[bytes] = []
    speech_started = False
    silence_frames = 0
    max_frames = int(sample_rate / vad._frame_size * max_duration)
    silence_limit = int(silence_after * 1000 / vad.config.frame_duration_ms)

    try:
        for _ in range(max_frames):
            data = stream.read(vad._frame_size, exception_on_overflow=False)
            is_speech = vad.is_speech(data)

            if is_speech:
                speech_started = True
                silence_frames = 0
                frames.append(data)
            elif speech_started:
                silence_frames += 1
                frames.append(data)  # Include trailing silence for natural speech

                if silence_frames >= silence_limit:
                    break
    finally:
        stream.stop_stream()
        stream.close()
        pa.terminate()

    if not frames:
        return AudioBuffer(
            samples=[],
            sample_rate=sample_rate,
            duration_seconds=0.0,
            channels=1,
        )

    # Convert to float32 samples
    all_bytes = b"".join(frames)
    sample_count = len(all_bytes) // 2  # 2 bytes per int16
    int16_samples = struct.unpack(f"{sample_count}h", all_bytes)
    samples = [s / 32767.0 for s in int16_samples]

    duration = len(samples) / sample_rate

    return AudioBuffer(
        samples=samples,
        sample_rate=sample_rate,
        duration_seconds=duration,
        channels=1,
    )
