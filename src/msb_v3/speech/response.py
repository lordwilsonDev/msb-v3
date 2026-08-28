"""Voice response pipeline — the full loop.

listen → think → speak

The system hears a command, processes it, and speaks the result back.
This is the Voice Identity Workstation's core interaction loop.

Usage::

    from msb_v3.speech.response import VoiceResponder

    responder = VoiceResponder()

    # From a file
    result = responder.respond_to_file("recording.wav")

    # From a transcript
    result = responder.respond_to_text("What is the system status?")

    # Custom processor
    result = responder.respond_to_text(
        "Research AI inference",
        processor=lambda cmd: {"summary": "Found 3 papers on local inference."},
    )
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from msb_v3.speech.audio import (
    AudioRendererRegistry,
    RenderContext,
    RenderStatus,
    silent_payload,
)
from msb_v3.speech.intent import extract_intent
from msb_v3.speech.models import Transcript, VoiceCommand
from msb_v3.speech.pipeline import SpeechPipeline
from msb_v3.speech.safety import (
    PolicyAction,
    VoicePolicyDecision,
    VoicePolicyGate,
    VoiceSession,
    classify_risk,
)
from msb_v3.speech.tts.engine import speak


@dataclass
class VoiceResponse:
    """A complete voice interaction — input through output."""

    # Input
    input_text: str = ""
    input_audio_path: str = ""

    # Processing
    command: Optional[VoiceCommand] = None
    authorized: bool = False
    authorization_reason: str = ""

    # Output
    response_text: str = ""
    response_audio_path: str = ""
    spoken: bool = False

    # Metadata
    latency_ms: float = 0.0
    timestamp: str = ""
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "input": self.input_text,
            "command": {
                "endpoint": self.command.endpoint if self.command else "",
                "params": self.command.params if self.command else {},
            },
            "authorized": self.authorized,
            "response": self.response_text,
            "spoken": self.spoken,
            "latency_ms": self.latency_ms,
        }


# Default response templates
_RESPONSES = {
    "empty": "I didn't catch that. Could you repeat?",
    "denied": "Access denied. Speaker not authorized.",
    "error": "Sorry, something went wrong processing your request.",
    "help": (
        "I can help with research, system status, deployments, "
        "and general questions. Just speak your command."
    ),
}


class VoiceResponder:
    """Full voice interaction loop: listen → think → speak.

    The responder connects the speech pipeline (STT + verification)
    to a response generator and TTS output, with Vesta-integrated
    policy gating for every command.
    """

    def __init__(
        self,
        pipeline: Optional[SpeechPipeline] = None,
        voice: Optional[str] = None,
        tts_rate: int = 200,
        speak_aloud: bool = True,
        policy_gate: Optional[VoicePolicyGate] = None,
        audio_registry: Optional[AudioRendererRegistry] = None,
    ) -> None:
        self.pipeline = pipeline or SpeechPipeline()
        self.voice = voice
        self.tts_rate = tts_rate
        self.speak_aloud = speak_aloud
        self.policy_gate = policy_gate or VoicePolicyGate(require_speaker=True)
        self.audio_registry = audio_registry or AudioRendererRegistry()

    def respond_to_file(
        self,
        audio_path: str,
        processor: Optional[Callable[[VoiceCommand], Dict[str, Any]]] = None,
    ) -> VoiceResponse:
        """Process an audio file and speak the response."""
        start = time.monotonic()
        response = VoiceResponse(input_audio_path=audio_path)

        # Run the speech pipeline
        result = self.pipeline.process_file(audio_path)
        response.authorized = result.authorized
        response.authorization_reason = result.authorization_reason

        if result.transcript:
            response.input_text = result.transcript.text

        if not result.authorized:
            response.response_text = _RESPONSES["denied"]
            response.error = result.authorization_reason
        elif result.error:
            response.response_text = _RESPONSES["error"]
            response.error = result.error
        elif result.command:
            response.command = result.command
            response.response_text = self._generate_response(result.command, processor)
        else:
            response.response_text = _RESPONSES["empty"]

        # Speak the response
        if self.speak_aloud and response.response_text:
            response.spoken = self._render_response(response.response_text)

        response.latency_ms = (time.monotonic() - start) * 1000
        response.timestamp = result.timestamp
        return response

    def respond_to_text(
        self,
        text: str,
        processor: Optional[Callable[[VoiceCommand], Dict[str, Any]]] = None,
        confirmed: bool = False,
    ) -> VoiceResponse:
        """Process a text transcript and speak the response.

        Every command passes through VoicePolicyGate before execution.
        HIGH/CRITICAL commands return a confirmation prompt.
        """
        start = time.monotonic()
        response = VoiceResponse(input_text=text)

        if not text or not text.strip():
            response.response_text = _RESPONSES["empty"]
            if self.speak_aloud:
                response.spoken = speak(response.response_text, voice=self.voice, rate=self.tts_rate)
            response.latency_ms = (time.monotonic() - start) * 1000
            return response

        # Extract intent
        transcript = Transcript(text=text, language="en", confidence=0.9, engine="text")
        command = extract_intent(transcript)
        response.command = command

        # Policy gate — every command goes through safety
        if command.endpoint:
            decision = self.policy_gate.evaluate(
                command.endpoint,
                command.params,
                transcript_confidence=0.9,
                speaker_confidence=0.9,
                speaker_enrolled=True,
                intent_confidence=0.9,
                confirmed=confirmed,
            )

            if decision.action == PolicyAction.DENY:
                response.response_text = self._deny_message(decision)
                response.authorized = False
                response.error = "; ".join(decision.reasons)
            elif decision.action == PolicyAction.CONFIRM:
                response.response_text = self._confirm_message(command, decision)
                response.authorized = False
            else:
                # ALLOW — generate response
                response.response_text = self._generate_response(command, processor)
                response.authorized = True
        else:
            response.response_text = self._generate_response(command, processor)
            response.authorized = True

        # Speak
        if self.speak_aloud and response.response_text:
            response.spoken = self._render_response(response.response_text)

        response.latency_ms = (time.monotonic() - start) * 1000
        return response

    def respond_with_session(
        self,
        text: str,
        confirmed: bool = False,
    ) -> VoiceSession:
        """Process text with full per-stage timing in VoiceSession.

        Returns a VoiceSession with latency_breakdown populated.
        This is the instrumented version for observability.
        """
        session = VoiceSession()
        total_start = time.monotonic()

        # Stage 1: Intent extraction
        t0 = time.monotonic()
        transcript = Transcript(text=text, language="en", confidence=0.9, engine="text")
        command = extract_intent(transcript)
        session.latency_intent_ms = (time.monotonic() - t0) * 1000

        # Populate session fields
        session.transcript_text = text
        session.transcript_engine = "text"
        session.transcription_confidence = 0.9
        session.speaker_id = "api"
        session.speaker_confidence = 0.9
        session.speaker_enrolled = True
        session.intent_endpoint = command.endpoint or ""
        session.intent_params = command.params
        session.intent_command = command.command
        session.intent_confidence = 0.9

        # Stage 2: Policy gate
        t0 = time.monotonic()
        if command.endpoint:
            decision = self.policy_gate.evaluate(
                command.endpoint,
                command.params,
                transcript_confidence=0.9,
                speaker_confidence=0.9,
                speaker_enrolled=True,
                intent_confidence=0.9,
                confirmed=confirmed,
            )
        else:
            decision = None
        session.latency_policy_ms = (time.monotonic() - t0) * 1000

        # Stage 3: Response generation
        t0 = time.monotonic()
        if decision and decision.action == PolicyAction.DENY:
            session.response_text = self._deny_message(decision)
            session.risk_level = decision.risk_level.value
            session.policy_action = decision.action.value
            session.policy_reasons = decision.reasons
        elif decision and decision.action == PolicyAction.CONFIRM:
            session.response_text = self._confirm_message(command, decision)
            session.risk_level = decision.risk_level.value
            session.policy_action = decision.action.value
            session.policy_reasons = decision.reasons
            session.requires_confirmation = True
        else:
            session.response_text = self._generate_response(command, None)
            session.risk_level = classify_risk(command.endpoint or "").value
            session.policy_action = "ALLOW"
            session.executed = True
        session.latency_execute_ms = (time.monotonic() - t0) * 1000

        # Stage 4: TTS
        t0 = time.monotonic()
        if self.speak_aloud and session.response_text:
            session.spoken = speak(
                session.response_text,
                voice=self.voice,
                rate=self.tts_rate,
            )
        session.latency_tts_ms = (time.monotonic() - t0) * 1000

        # Overall confidence
        session.overall_confidence = 0.9 if session.executed else 0.0

        # Total latency
        session.latency_total_ms = (time.monotonic() - total_start) * 1000

        return session

    def _render_response(self, text: str) -> bool:
        """Render a response through the injected audio seam.

        The legacy TTS adapter remains the default compatibility path until a
        renderer is registered; callers can inject a registry for conformance
        tests or native playback without changing governance.
        """
        if self.audio_registry.has_available_renderer(frozenset({"local_playback"})):
            result = self.audio_registry.render(
                silent_payload(),
                RenderContext(),
                frozenset({"local_playback"}),
            )
            return result.status == RenderStatus.PLAYED
        return speak(text, voice=self.voice, rate=self.tts_rate)

    def _deny_message(self, decision: VoicePolicyDecision) -> str:
        """Generate a spoken denial message."""
        risk = decision.risk_level.value
        reasons = decision.reasons
        if any("transcription confidence" in r for r in reasons):
            return "I'm not sure I heard that correctly. Could you repeat?"
        if any("speaker confidence" in r for r in reasons):
            return "Speaker not recognized. Access denied."
        if any("intent confidence" in r for r in reasons):
            return "I'm not sure what you want. Could you rephrase?"
        return f"Command denied. Risk level: {risk}."

    def _confirm_message(
        self, command: VoiceCommand, decision: VoicePolicyDecision
    ) -> str:
        """Generate a spoken confirmation prompt for high-risk commands."""
        risk = decision.risk_level.value
        endpoint = command.endpoint or ""

        if "/killswitch" in endpoint:
            return (
                "Warning: this will halt the system. "
                "Say 'confirm' to proceed, or 'cancel' to abort."
            )
        if "/governance/execute" in endpoint:
            action = command.params.get("action", "this action")
            return (
                f"This will execute {action}. "
                "Say 'confirm' to proceed, or 'cancel' to abort."
            )
        return (
            f"This is a {risk} risk command. "
            "Say 'confirm' to proceed, or 'cancel' to abort."
        )

    def _generate_response(
        self,
        command: VoiceCommand,
        processor: Optional[Callable[[VoiceCommand], Dict[str, Any]]] = None,
    ) -> str:
        """Generate a spoken response from a command."""
        if command.command == "help":
            return _RESPONSES["help"]

        if command.command == "empty":
            return _RESPONSES["empty"]

        if processor:
            try:
                result = processor(command)
                return self._format_response(command, result)
            except Exception:  # noqa: BLE001
                return _RESPONSES["error"]

        # Default responses based on command type
        endpoint = command.endpoint or ""
        params = command.params

        if "/research" in endpoint:
            topic = params.get("topic", "your request").rstrip(".")
            return f"Starting research on {topic}. I'll have results shortly."

        if "/system/health" in endpoint:
            return "Checking system status."

        if "/governance/execute" in endpoint:
            action = params.get("action", "requested action")
            return f"Executing {action}. Please wait."

        if "/governance/killswitch" in endpoint:
            if "arm" in endpoint:
                return "Kill switch engaged. System halted."
            return "Kill switch disarmed. System resumed."

        if "/flywheel" in endpoint:
            return "Starting flywheel research cycle."

        if "/chat" in endpoint:
            query = params.get("query", "your question")
            return f"Processing your question: {query}"

        return f"Command received: {command.command}"

    def _format_response(
        self, command: VoiceCommand, result: Dict[str, Any]
    ) -> str:
        """Format a processor result into spoken text."""
        if "summary" in result:
            return result["summary"]
        if "text" in result:
            return result["text"]
        if "error" in result:
            return f"Error: {result['error']}"
        return f"Command completed: {command.command}"
