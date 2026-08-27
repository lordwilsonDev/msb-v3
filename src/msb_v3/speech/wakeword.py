"""Wake word detection — detect activation phrase to start listening.

Listens for "Hey Sovereign" (or "sovereign") to activate the voice system.
Uses a simple approach: continuous low-power listening with whisper tiny
for wake word detection, then full processing for the actual command.

Architecture:
    Microphone → VAD → whisper tiny → wake word check → full pipeline

Usage::

    from msb_v3.speech.wakeword import WakeWordDetector

    detector = WakeWordDetector()
    if detector.is_wake_word("Hey Sovereign, what's the status?"):
        # Process the command after the wake word
        command = detector.extract_command("Hey Sovereign, what's the status?")
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

# Wake word patterns (case-insensitive)
_WAKE_PATTERNS = [
    r"\bhey\s+sovereign\b",
    r"\bsovereign\b",
    r"\bhey\s+msb\b",
    r"\bmsb\b",
]


@dataclass
class WakeWordResult:
    """Result of wake word detection."""

    detected: bool
    wake_word: str = ""
    confidence: float = 0.0
    raw_text: str = ""
    command_text: str = ""

    def as_dict(self) -> dict:
        return {
            "detected": self.detected,
            "wake_word": self.wake_word,
            "confidence": self.confidence,
            "raw_text": self.raw_text,
            "command_text": self.command_text,
        }


class WakeWordDetector:
    """Detect wake word in transcribed text.

    This is a text-based wake word detector — it checks if a transcription
    contains the wake word and extracts the command after it.

    For real-time audio wake word detection, use VoiceStreamDetector which
    combines VAD + whisper for continuous listening.
    """

    def __init__(
        self,
        wake_words: Optional[List[str]] = None,
        case_sensitive: bool = False,
    ) -> None:
        self.wake_words = wake_words or ["sovereign", "msb"]
        self.case_sensitive = case_sensitive
        self._patterns = self._compile_patterns()

    def _compile_patterns(self) -> List[re.Pattern]:
        """Compile wake word regex patterns."""
        flags = 0 if self.case_sensitive else re.IGNORECASE
        patterns = []
        for word in self.wake_words:
            # Match "hey <word>" or just "<word>"
            pattern = rf"\b(?:hey\s+)?{re.escape(word)}\b"
            patterns.append(re.compile(pattern, flags))
        return patterns

    def detect(self, text: str) -> WakeWordResult:
        """Check if text contains a wake word.

        Returns WakeWordResult with detected=True if found,
        and the command text after the wake word.
        """
        if not text or not text.strip():
            return WakeWordResult(detected=False, raw_text=text or "")

        for pattern in self._patterns:
            match = pattern.search(text)
            if match:
                wake_word = match.group(0)
                # Extract command after wake word
                after = text[match.end():].strip()
                # Remove leading punctuation/comma
                after = re.sub(r"^[,.\s]+", "", after)

                return WakeWordResult(
                    detected=True,
                    wake_word=wake_word,
                    confidence=0.9,
                    raw_text=text,
                    command_text=after if after else "",
                )

        return WakeWordResult(detected=False, raw_text=text)

    def extract_command(self, text: str) -> str:
        """Extract the command text after the wake word.

        Returns empty string if no wake word found.
        """
        result = self.detect(text)
        return result.command_text if result.detected else ""


class VoiceStreamDetector:
    """Continuous voice stream detector with wake word + VAD.

    Combines:
    - VAD for speech detection
    - Whisper tiny for transcription
    - Wake word detection
    - Full pipeline for command processing

    Usage::

        detector = VoiceStreamDetector()
        # In a loop:
        result = detector.process_frame(audio_frame)
        if result.state == "COMMAND_READY":
            # Process the command
            process(result.command_text)
    """

    def __init__(self) -> None:
        self.wake_detector = WakeWordDetector()
        self.state = "WAITING"  # WAITING → LISTENING → COMMAND_READY
        self._command_buffer: List[str] = []
        self._silence_count = 0
        self._max_silence = 5  # frames of silence before reset

    def process_frame(
        self, text: str, is_speech: bool = True
    ) -> "StreamFrameResult":
        """Process a transcribed frame.

        State machine:
        - WAITING: listen for wake word
        - LISTENING: wake word detected, collecting command
        - COMMAND_READY: command complete, ready to process
        """
        result = StreamFrameResult(state=self.state)

        if self.state == "WAITING":
            wake = self.wake_detector.detect(text)
            if wake.detected:
                self.state = "LISTENING"
                result.state = "LISTENING"
                # If command was included with wake word
                if wake.command_text:
                    self._command_buffer.append(wake.command_text)
                    self._silence_count = 0
            else:
                result.state = "WAITING"

        elif self.state == "LISTENING":
            if is_speech and text.strip():
                self._command_buffer.append(text.strip())
                self._silence_count = 0
            else:
                self._silence_count += 1

            # If silence after speech, command is complete
            if self._silence_count >= self._max_silence and self._command_buffer:
                self.state = "COMMAND_READY"
                result.state = "COMMAND_READY"
                result.command_text = " ".join(self._command_buffer)

        elif self.state == "COMMAND_READY":
            result.state = "COMMAND_READY"
            result.command_text = " ".join(self._command_buffer)

        return result

    def reset(self) -> None:
        """Reset to waiting state."""
        self.state = "WAITING"
        self._command_buffer.clear()
        self._silence_count = 0


@dataclass
class StreamFrameResult:
    """Result of processing a single frame in the voice stream."""

    state: str  # WAITING, LISTENING, COMMAND_READY
    command_text: str = ""

    def as_dict(self) -> dict:
        return {
            "state": self.state,
            "command_text": self.command_text,
        }
