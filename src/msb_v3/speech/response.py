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

from msb_v3.speech.intent import extract_intent
from msb_v3.speech.models import Transcript, VoiceCommand
from msb_v3.speech.pipeline import SpeechPipeline
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
    to a response generator and TTS output.
    """

    def __init__(
        self,
        pipeline: Optional[SpeechPipeline] = None,
        voice: Optional[str] = None,
        tts_rate: int = 200,
        speak_aloud: bool = True,
    ) -> None:
        self.pipeline = pipeline or SpeechPipeline()
        self.voice = voice
        self.tts_rate = tts_rate
        self.speak_aloud = speak_aloud

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
            response.spoken = speak(
                response.response_text,
                voice=self.voice,
                rate=self.tts_rate,
            )

        response.latency_ms = (time.monotonic() - start) * 1000
        response.timestamp = result.timestamp
        return response

    def respond_to_text(
        self,
        text: str,
        processor: Optional[Callable[[VoiceCommand], Dict[str, Any]]] = None,
    ) -> VoiceResponse:
        """Process a text transcript and speak the response."""
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

        # Generate response
        response.response_text = self._generate_response(command, processor)

        # Speak
        if self.speak_aloud and response.response_text:
            response.spoken = speak(
                response.response_text,
                voice=self.voice,
                rate=self.tts_rate,
            )

        response.authorized = True
        response.latency_ms = (time.monotonic() - start) * 1000
        return response

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
            topic = params.get("topic", "your request")
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
