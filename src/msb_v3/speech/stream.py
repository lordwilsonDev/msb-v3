"""Continuous voice stream — always-on listening loop.

Ties together VAD + whisper + wake word + speaker gate + full pipeline for
always-on voice interaction.

Architecture:
    Microphone → VAD → whisper tiny → StreamGate → full pipeline → TTS

The gate (:mod:`msb_v3.speech.gate`) decides who may command, sleep, or
wake the agent. Only an enrolled speaker with a ``hey``-prefixed wake
phrase reaches the pipeline.

Usage::

    from msb_v3.speech.stream import VoiceStream

    stream = VoiceStream()
    # Run in a loop:
    for result in stream.listen_continuous():
        if result.command_text:
            print(f"Got command: {result.command_text}")
            print(f"Response: {result.response_text}")
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, List, Optional

from msb_v3.speech.gate import GateAction, NotEnrolledError, StreamGate
from msb_v3.speech.response import VoiceResponder
from msb_v3.speech.safety import VoiceSession
from msb_v3.speech.speaker import SpeakerVerifier
from msb_v3.speech.transcribe import transcribe
from msb_v3.speech.vad import VADConfig, VoiceDetector, capture_until_silence
from msb_v3.speech.wakeword import VoiceStreamDetector

#: Spoken when the gate changes state, so the owner gets feedback.
_SLEEP_ANNOUNCEMENT = "Going to sleep."
_WAKE_ANNOUNCEMENT = "I'm awake."


@dataclass
class StreamResult:
    """Result from one iteration of the voice stream."""

    state: str  # WAITING, LISTENING, PROCESSING, RESPONDING
    command_text: str = ""
    response_text: str = ""
    session: Optional[VoiceSession] = None
    error: str = ""
    latency_ms: float = 0.0
    action: str = ""  # IGNORE, SLEEP, WAKE, COMMAND — empty before the gate
    speaker_id: str = ""
    speaker_confidence: float = 0.0

    def as_dict(self) -> dict:
        return {
            "state": self.state,
            "command_text": self.command_text,
            "response_text": self.response_text,
            "has_session": self.session is not None,
            "error": self.error,
            "latency_ms": self.latency_ms,
            "action": self.action,
            "speaker_id": self.speaker_id,
            "speaker_confidence": self.speaker_confidence,
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
    enrollments_path: Optional[str] = None


class VoiceStream:
    """Always-on voice stream with wake word + speaker gate + VAD pipeline.

    Combines:
    - VAD for intelligent capture (variable length)
    - Whisper for transcription
    - StreamGate for speaker-verified command / sleep / wake decisions
    - VoiceResponder for command processing + TTS
    - VoiceSession for audit trail

    Fail-closed at start-up: with nobody enrolled, ``listen_once`` and
    ``listen_continuous`` raise :class:`NotEnrolledError` before touching
    the microphone, rather than appear to listen to nobody.
    """

    def __init__(
        self,
        config: Optional[VoiceStreamConfig] = None,
        verifier: Optional[SpeakerVerifier] = None,
    ) -> None:
        self.config = config or VoiceStreamConfig()
        self.verifier = verifier or SpeakerVerifier(
            enrollments_path=self.config.enrollments_path,
        )
        self.gate = StreamGate(
            self.verifier,
            wake_words=self.config.wake_words,
        )
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

    @property
    def asleep(self) -> bool:
        """Whether the gate is asleep (in-memory; a restart starts awake)."""
        return self.gate.asleep

    def _ensure_enrolled(self) -> None:
        """Refuse to start when nobody is enrolled. Called before any capture."""
        if not self.verifier.list_speakers():
            raise NotEnrolledError(
                "always-on voice requires an enrolled speaker; "
                "enroll one with SpeakerVerifier.enroll() first, or the "
                "stream cannot tell the owner from anyone else"
            )

    def listen_once(self) -> StreamResult:
        """Listen for one utterance and process it.

        Captures audio until silence, transcribes, and hands the utterance to
        the gate. Only an enrolled speaker with a valid wake phrase gets a
        command through; sleep and wake phrases change the gate's state.

        Raises:
            NotEnrolledError: nobody is enrolled, so nothing could be verified.
                Raised before the microphone is touched.
        """
        # Outside the try below: a refusal is not an utterance-level error.
        self._ensure_enrolled()

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

            # Who is this, and what are they asking for?
            decision = self.gate.decide(transcript.text, audio)
            result.action = decision.action.value
            result.speaker_id = decision.speaker_id
            result.speaker_confidence = decision.speaker_confidence

            if decision.action is GateAction.IGNORE:
                result.state = "WAITING"
                result.error = decision.reason
                result.latency_ms = (time.monotonic() - start) * 1000
                return result

            if decision.action in (GateAction.SLEEP, GateAction.WAKE):
                result.state = "WAITING"
                if self.config.speak_aloud:
                    self.responder._render_response(
                        _SLEEP_ANNOUNCEMENT
                        if decision.action is GateAction.SLEEP
                        else _WAKE_ANNOUNCEMENT
                    )
                result.latency_ms = (time.monotonic() - start) * 1000
                return result

            # Process command
            result.state = "PROCESSING"
            result.command_text = decision.command_text

            session = self.responder.respond_with_session(decision.command_text)
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

        Raises:
            NotEnrolledError: nobody is enrolled. Raised before the first
                capture, so an unusable stream fails immediately.
        """
        self._ensure_enrolled()

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
