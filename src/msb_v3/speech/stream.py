"""Continuous voice stream — always-on listening loop.

Ties together VAD + whisper + wake word + full pipeline for
always-on voice interaction.

Architecture:
    Microphone → VAD → whisper tiny → wake word → full pipeline → TTS

Usage::

    from msb_v3.speech.stream import VoiceStream

    stream = VoiceStream()
    # Run in a loop:
    for result in stream.listen():
        if result.command:
            print(f"Got command: {result.command}")
            print(f"Response: {result.response}")
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, List, Optional

from msb_v3.speech.response import VoiceResponder
from msb_v3.speech.safety import VoiceSession
from msb_v3.speech.transcribe import transcribe
from msb_v3.speech.vad import VADConfig, VoiceDetector, capture_until_silence
from msb_v3.speech.wakeword import VoiceStreamDetector


@dataclass
class StreamResult:
    """Result from one iteration of the voice stream."""

    state: str  # WAITING, LISTENING, PROCESSING, RESPONDING
    command_text: str = ""
    response_text: str = ""
    session: Optional[VoiceSession] = None
    error: str = ""
    latency_ms: float = 0.0

    def as_dict(self) -> dict:
        return {
            "state": self.state,
            "command_text": self.command_text,
            "response_text": self.response_text,
            "has_session": self.session is not None,
            "error": self.error,
            "latency_ms": self.latency_ms,
        }


@dataclass
class VoiceStreamConfig:
    """Configuration for the voice stream."""

    whisper_model: str = "tiny"
    sample_rate: int = 16000
    silence_after: float = 1.0
    max_capture_duration: float = 10.0
    wake_words: Optional[List[str]] = None
    speak_aloud: bool = True
    continuous: bool = True


class VoiceStream:
    """Always-on voice stream with wake word + VAD + full pipeline.

    Combines:
    - VAD for intelligent capture (variable length)
    - Whisper for transcription
    - Wake word detection
    - VoiceResponder for command processing + TTS
    - VoiceSession for audit trail
    """

    def __init__(self, config: Optional[VoiceStreamConfig] = None) -> None:
        self.config = config or VoiceStreamConfig()
        self.vad = VoiceDetector(
            VADConfig(
                sample_rate=self.config.sample_rate,
                silence_threshold=self.config.silence_after,
                max_duration=self.config.max_capture_duration,
            )
        )
        self.stream_detector = VoiceStreamDetector()
        self.responder = VoiceResponder(
            speak_aloud=self.config.speak_aloud,
        )
        self._running = False

    def listen_once(self) -> StreamResult:
        """Listen for one utterance and process it.

        Captures audio until silence, transcribes, checks wake word,
        and processes if wake word detected.
        """
        start = time.monotonic()
        result = StreamResult(state="LISTENING")

        try:
            # Capture audio with VAD
            audio = capture_until_silence(
                duration_seconds=self.config.max_capture_duration,
                silence_after=self.config.silence_after,
                sample_rate=self.config.sample_rate,
            )

            if not audio.samples:
                result.state = "WAITING"
                result.error = "No audio captured"
                result.latency_ms = (time.monotonic() - start) * 1000
                return result

            # Transcribe
            transcript = transcribe(
                audio,
                model_name=self.config.whisper_model,
                engine="auto",
            )

            if not transcript or not transcript.text:
                result.state = "WAITING"
                result.error = "No speech detected"
                result.latency_ms = (time.monotonic() - start) * 1000
                return result

            # Check wake word
            from msb_v3.speech.wakeword import WakeWordDetector

            wake = WakeWordDetector().detect(transcript.text)

            if not wake.detected:
                result.state = "WAITING"
                result.error = "No wake word detected"
                result.latency_ms = (time.monotonic() - start) * 1000
                return result

            # Extract command
            command_text = wake.command_text
            if not command_text:
                result.state = "LISTENING"
                result.error = "Wake word detected but no command"
                result.latency_ms = (time.monotonic() - start) * 1000
                return result

            # Process command
            result.state = "PROCESSING"
            result.command_text = command_text

            session = self.responder.respond_with_session(command_text)
            result.session = session
            result.response_text = session.response_text
            result.state = "RESPONDING"
            result.latency_ms = (time.monotonic() - start) * 1000

        except Exception as exc:
            result.error = str(exc)
            result.latency_ms = (time.monotonic() - start) * 1000

        return result

    def listen_continuous(
        self,
        callback: Optional[Callable[[StreamResult], None]] = None,
        max_iterations: int = 100,
    ) -> List[StreamResult]:
        """Listen continuously, processing commands as they come.

        Args:
            callback: Called after each iteration with the result
            max_iterations: Safety limit to prevent infinite loops

        Returns:
            List of all results from the listening loop
        """
        results: List[StreamResult] = []
        self._running = True

        for i in range(max_iterations):
            if not self._running:
                break

            result = self.listen_once()
            results.append(result)

            if callback:
                callback(result)

            # Brief pause between iterations
            time.sleep(0.1)

        self._running = False
        return results

    def stop(self) -> None:
        """Stop the continuous listening loop."""
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running
