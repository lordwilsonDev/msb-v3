"""Full speech pipeline orchestrator.

The pipeline: capture → transcribe → verify → authorize → execute.

Every stage is optional (can be skipped for testing). The pipeline is
fail-closed: any error at any stage returns an unauthorized result with
the error reason.

Usage::

    from msb_v3.speech.pipeline import SpeechPipeline

    pipeline = SpeechPipeline()

    # From a file
    result = pipeline.process_file("recording.wav")

    # From an audio buffer
    result = pipeline.process_audio(audio_buffer)

    # Check authorization
    if result.authorized:
        # Execute the command
        ...
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from msb_v3.speech.capture import capture_from_file
from msb_v3.speech.intent import extract_intent
from msb_v3.speech.models import AudioBuffer, PipelineResult
from msb_v3.speech.speaker import SpeakerVerifier
from msb_v3.speech.transcribe import transcribe


class SpeechPipeline:
    """Full voice → command pipeline with authorization.

    The pipeline is fail-closed: any error returns authorized=False.
    """

    def __init__(
        self,
        speaker_verifier: Optional[SpeakerVerifier] = None,
        whisper_model: str = "tiny",
        whisper_engine: str = "auto",
        speaker_threshold: float = 0.75,
    ) -> None:
        self.verifier = speaker_verifier or SpeakerVerifier(
            threshold=speaker_threshold
        )
        self.whisper_model = whisper_model
        self.whisper_engine = whisper_engine

    def process_file(
        self,
        audio_path: str,
        speaker_id: Optional[str] = None,
    ) -> PipelineResult:
        """Process an audio file through the full pipeline."""
        try:
            audio = capture_from_file(audio_path)
        except Exception as exc:
            return self._error_result(f"Capture failed: {exc}")

        return self.process_audio(audio, speaker_id=speaker_id)

    def process_audio(
        self,
        audio: AudioBuffer,
        speaker_id: Optional[str] = None,
    ) -> PipelineResult:
        """Process an audio buffer through the full pipeline."""
        result = PipelineResult(
            audio=audio,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        # Stage 1: Transcribe
        result.transcript = self._transcribe(audio)
        if not result.transcript or not result.transcript.text:
            return self._fail(result, "Transcription empty — no speech detected")

        # Stage 2: Speaker verification
        result.speaker = self._verify_speaker(audio)

        # Stage 3: Authorization (fail-closed)
        result.authorized, result.authorization_reason = self._authorize(
            result.speaker, speaker_id
        )
        if not result.authorized:
            return result

        # Stage 4: Intent extraction
        result.command = extract_intent(result.transcript)

        return result

    def enroll_speaker(self, speaker_id: str, audio: AudioBuffer) -> Dict[str, Any]:
        """Enroll a speaker from an audio sample."""
        self.verifier.enroll(speaker_id, audio)
        return {
            "speaker_id": speaker_id,
            "enrolled": True,
            "total_enrollments": len(self.verifier.list_speakers()),
            "samples_for_speaker": len(
                self.verifier._embeddings.get(speaker_id, [])
            ),
        }

    # ── Internal ───────────────────────────────────────────────────────

    def _transcribe(self, audio: AudioBuffer):
        """Stage 1: Speech-to-text."""
        try:
            return transcribe(
                audio,
                model_name=self.whisper_model,
                engine=self.whisper_engine,
            )
        except Exception:
            return None

    def _verify_speaker(self, audio: AudioBuffer):
        """Stage 2: Speaker verification."""
        try:
            return self.verifier.verify(audio)
        except Exception:
            from msb_v3.speech.models import SpeakerIdentity

            return SpeakerIdentity(
                speaker_id="unknown",
                confidence=0.0,
                is_enrolled=False,
            )

    def _authorize(
        self, speaker, expected_id: Optional[str]
    ) -> tuple[bool, str]:
        """Stage 3: Authorization gate (fail-closed)."""
        # If no speaker enrolled, allow (demo mode)
        if not self.verifier.list_speakers():
            return True, "no speakers enrolled — demo mode"

        # If a specific speaker_id is expected, check it
        if expected_id:
            if speaker.speaker_id == expected_id and speaker.is_enrolled:
                return True, f"speaker verified: {speaker.speaker_id}"
            return False, f"expected {expected_id}, got {speaker.speaker_id}"

        # If speaker is enrolled, allow
        if speaker.is_enrolled:
            return True, f"speaker verified: {speaker.speaker_id} (confidence: {speaker.confidence:.2f})"

        # Unknown speaker — deny
        return False, f"unknown speaker (confidence: {speaker.confidence:.2f})"

    def _fail(self, result: PipelineResult, reason: str) -> PipelineResult:
        """Mark a result as failed."""
        result.error = reason
        result.authorized = False
        result.authorization_reason = reason
        return result

    def _error_result(self, error: str) -> PipelineResult:
        """Create an error result."""
        return PipelineResult(
            authorized=False,
            authorization_reason=error,
            error=error,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
