"""Barge-in support — interrupt TTS when user starts speaking.

Allows the user to interrupt the system while it's speaking.
When speech is detected during TTS playback, the TTS is stopped
and the new speech is processed immediately.

Architecture:
    TTS playing → VAD detects speech → stop TTS → process new input

Usage::

    from msb_v3.speech.bargein import BargeInController

    controller = BargeInController()
    # During TTS:
    controller.start_monitoring()
    # If user speaks:
    if controller.should_interrupt():
        stop_tts()
        process_new_input()
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Optional

from msb_v3.speech.vad import VoiceDetector, VADConfig


@dataclass
class BargeInConfig:
    """Barge-in configuration."""

    check_interval_ms: float = 100.0  # How often to check for interruption
    speech_threshold: float = 0.3  # Fraction of frames that must be speech
    min_speech_frames: int = 3  # Minimum frames to trigger interrupt
    enabled: bool = True


class BargeInController:
    """Monitor for barge-in during TTS playback.

    When TTS is playing, this controller monitors the microphone
    for speech. If speech is detected, it signals that TTS should
    be interrupted.
    """

    def __init__(self, config: Optional[BargeInConfig] = None) -> None:
        self.config = config or BargeInConfig()
        self.vad = VoiceDetector(
            VADConfig(
                aggressiveness=3,  # Aggressive — only real speech
                frame_duration_ms=20,
            )
        )
        self._monitoring = False
        self._interrupted = False
        self._monitor_thread: Optional[threading.Thread] = None
        self._speech_count = 0
        self._total_frames = 0

    def start_monitoring(self) -> None:
        """Start monitoring for barge-in.

        Call this when TTS starts playing.
        """
        if not self.config.enabled:
            return

        self._monitoring = True
        self._interrupted = False
        self._speech_count = 0
        self._total_frames = 0

        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, daemon=True
        )
        self._monitor_thread.start()

    def stop_monitoring(self) -> None:
        """Stop monitoring for barge-in.

        Call this when TTS finishes.
        """
        self._monitoring = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=1.0)
            self._monitor_thread = None

    def should_interrupt(self) -> bool:
        """Check if TTS should be interrupted."""
        return self._interrupted

    def reset(self) -> None:
        """Reset the interrupt state."""
        self._interrupted = False
        self._speech_count = 0
        self._total_frames = 0

    @property
    def is_monitoring(self) -> bool:
        return self._monitoring

    def _monitor_loop(self) -> None:
        """Background thread that monitors for barge-in."""
        try:
            import pyaudio

            pa = pyaudio.PyAudio()
            stream = pa.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=16000,
                input=True,
                frames_per_buffer=320,  # 20ms at 16kHz
            )

            try:
                while self._monitoring and not self._interrupted:
                    data = stream.read(320, exception_on_overflow=False)
                    is_speech = self.vad.is_speech(data)

                    self._total_frames += 1
                    if is_speech:
                        self._speech_count += 1

                    # Check if enough speech to trigger interrupt
                    if (
                        self._total_frames >= self.config.min_speech_frames
                        and self._speech_count / self._total_frames
                        >= self.config.speech_threshold
                    ):
                        self._interrupted = True
                        break

                    time.sleep(self.config.check_interval_ms / 1000)
            finally:
                stream.stop_stream()
                stream.close()
                pa.terminate()
        except Exception:
            # If mic unavailable, barge-in disabled
            pass


class TTSInterrupter:
    """Wrapper around TTS that supports barge-in.

    Wraps the speak() function to check for interruption.
    """

    def __init__(
        self,
        voice: Optional[str] = None,
        rate: int = 200,
    ) -> None:
        self.voice = voice
        self.rate = rate
        self.controller = BargeInController()

    def speak_with_barge_in(self, text: str) -> bool:
        """Speak text with barge-in support.

        Returns True if completed, False if interrupted.
        """
        from msb_v3.speech.tts.engine import speak

        self.controller.start_monitoring()
        try:
            # For macOS say, we can't easily interrupt mid-speech
            # So we check before and after
            result = speak(text, voice=self.voice, rate=self.rate)

            if self.controller.should_interrupt():
                return False  # Interrupted

            return result
        finally:
            self.controller.stop_monitoring()

    @property
    def was_interrupted(self) -> bool:
        return self.controller.should_interrupt()
