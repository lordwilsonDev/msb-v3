"""Tests for wake word detection."""

from __future__ import annotations

from msb_v3.speech.wakeword import (
    StreamFrameResult,
    VoiceStreamDetector,
    WakeWordDetector,
)


class TestWakeWordDetector:
    def setup_method(self):
        self.detector = WakeWordDetector()

    def test_detect_hey_sovereign(self):
        result = self.detector.detect("Hey Sovereign, what's the status?")
        assert result.detected is True
        assert "sovereign" in result.wake_word.lower()
        assert result.command_text == "what's the status?"

    def test_detect_just_sovereign(self):
        result = self.detector.detect("Sovereign, deploy canary")
        assert result.detected is True
        assert result.command_text == "deploy canary"

    def test_detect_hey_msb(self):
        result = self.detector.detect("Hey MSB, system status")
        assert result.detected is True
        assert result.command_text == "system status"

    def test_no_wake_word(self):
        result = self.detector.detect("What is the system status?")
        assert result.detected is False
        assert result.command_text == ""

    def test_empty_text(self):
        result = self.detector.detect("")
        assert result.detected is False

    def test_none_text(self):
        result = self.detector.detect(None)  # type: ignore
        assert result.detected is False

    def test_extract_command(self):
        cmd = self.detector.extract_command("Hey Sovereign, research AI")
        assert cmd == "research AI"

    def test_extract_command_no_wake(self):
        cmd = self.detector.extract_command("Just a regular question")
        assert cmd == ""

    def test_case_insensitive(self):
        result = self.detector.detect("hey SOVEREIGN status")
        assert result.detected is True

    def test_wake_word_at_end(self):
        result = self.detector.detect("Status please, Sovereign")
        assert result.detected is True

    def test_multiple_wake_words(self):
        detector = WakeWordDetector(wake_words=["computer", "jarvis"])
        result = detector.detect("Computer, what time is it?")
        assert result.detected is True
        assert result.command_text == "what time is it?"

    def test_result_serializes(self):
        result = self.detector.detect("Hey Sovereign, status")
        d = result.as_dict()
        assert d["detected"] is True
        assert "command_text" in d


class TestVoiceStreamDetector:
    def setup_method(self):
        self.detector = VoiceStreamDetector()

    def test_initial_state(self):
        assert self.detector.state == "WAITING"

    def test_wake_word_transitions_to_listening(self):
        result = self.detector.process_frame("Hey Sovereign")
        assert result.state == "LISTENING"
        assert self.detector.state == "LISTENING"

    def test_command_collected_after_wake(self):
        self.detector.process_frame("Hey Sovereign")
        self.detector.process_frame("deploy canary")
        assert self.detector.state == "LISTENING"

    def test_silence_completes_command(self):
        self.detector.process_frame("Hey Sovereign")
        self.detector.process_frame("deploy canary")
        # Simulate silence frames
        for _ in range(6):
            result = self.detector.process_frame("", is_speech=False)
        assert result.state == "COMMAND_READY"
        assert "deploy canary" in result.command_text

    def test_reset(self):
        self.detector.process_frame("Hey Sovereign")
        self.detector.reset()
        assert self.detector.state == "WAITING"

    def test_wake_word_with_command(self):
        # Wake word + command in same utterance
        result = self.detector.process_frame("Hey Sovereign, status")
        assert self.detector.state == "LISTENING"
        # Then silence
        for _ in range(6):
            result = self.detector.process_frame("", is_speech=False)
        assert result.state == "COMMAND_READY"
        assert "status" in result.command_text

    def test_stream_frame_serializes(self):
        result = StreamFrameResult(state="WAITING")
        d = result.as_dict()
        assert d["state"] == "WAITING"
