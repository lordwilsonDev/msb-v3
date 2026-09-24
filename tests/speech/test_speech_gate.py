"""Tests for the speaker-verified stream gate.

The gate is pure decision logic — no microphone, no transcription, no TTS,
no resemblyzer. The verifier is duck-typed, so a fake stands in for
``SpeakerVerifier`` and these tests run without the ``.[speech]`` extra.
"""

from __future__ import annotations

import pytest

from msb_v3.speech.gate import (
    GateAction,
    GateDecision,
    NotEnrolledError,
    StreamGate,
)
from msb_v3.speech.models import AudioBuffer, SpeakerIdentity


def _audio() -> AudioBuffer:
    """Any audio buffer — the fake verifier ignores its contents."""
    return AudioBuffer(samples=[0.0] * 160, sample_rate=16000)


class FakeVerifier:
    """Duck-typed stand-in for SpeakerVerifier.

    Counts ``verify`` calls so tests can assert the verifier is never
    consulted when the wake phrase is missing.
    """

    def __init__(
        self,
        speakers: tuple[str, ...] = ("wilson",),
        is_enrolled: bool = True,
        speaker_id: str = "wilson",
        confidence: float = 0.91,
        raises: bool = False,
    ) -> None:
        self._speakers = list(speakers)
        self._is_enrolled = is_enrolled
        self._speaker_id = speaker_id
        self._confidence = confidence
        self._raises = raises
        self.verify_calls = 0

    def set_enrolled(self, value: bool) -> None:
        """Simulate a different voice arriving at the microphone."""
        self._is_enrolled = value

    def list_speakers(self) -> list[str]:
        return list(self._speakers)

    def verify(self, audio: AudioBuffer) -> SpeakerIdentity:
        self.verify_calls += 1
        if self._raises:
            raise RuntimeError("embedding backend exploded")
        return SpeakerIdentity(
            speaker_id=self._speaker_id if self._is_enrolled else "unknown",
            confidence=self._confidence,
            is_enrolled=self._is_enrolled,
            embedding=[0.1, 0.2, 0.3],
            method="fake",
        )


class TestGateAction:
    def test_is_a_string_enum(self):
        assert GateAction.IGNORE == "IGNORE"
        assert GateAction.COMMAND.value == "COMMAND"

    def test_has_all_four_actions(self):
        assert {a.name for a in GateAction} == {
            "IGNORE",
            "SLEEP",
            "WAKE",
            "COMMAND",
        }


class TestNotEnrolledError:
    def test_is_a_runtime_error(self):
        assert issubclass(NotEnrolledError, RuntimeError)


class TestGateDecision:
    def test_defaults(self):
        d = GateDecision(action=GateAction.IGNORE)
        assert d.command_text == ""
        assert d.reason == ""
        assert d.speaker_id == "unknown"
        assert d.speaker_confidence == 0.0


class TestWakePhrase:
    """Step 1 — the wake phrase is checked before the speaker is."""

    def setup_method(self):
        self.verifier = FakeVerifier()
        self.gate = StreamGate(self.verifier)

    def test_no_wake_word_is_ignored(self):
        d = self.gate.decide("what is the system status", _audio())
        assert d.action == GateAction.IGNORE
        assert d.reason == "no wake word"

    def test_verifier_is_not_called_without_a_wake_word(self):
        self.gate.decide("what is the system status", _audio())
        assert self.verifier.verify_calls == 0

    def test_bare_wake_word_is_not_enough(self):
        # require_prefix: "msb" alone must not activate the agent.
        d = self.gate.decide("msb, what is the status", _audio())
        assert d.action == GateAction.IGNORE
        assert d.reason == "no wake word"
        assert self.verifier.verify_calls == 0

    def test_bare_sovereign_is_not_enough(self):
        d = self.gate.decide("sovereign, what is the status", _audio())
        assert d.action == GateAction.IGNORE
        assert d.reason == "no wake word"

    def test_hey_prefixed_wake_word_is_accepted(self):
        d = self.gate.decide("hey sovereign, system status", _audio())
        assert d.action == GateAction.COMMAND

    def test_empty_command_is_ignored(self):
        d = self.gate.decide("hey sovereign", _audio())
        assert d.action == GateAction.IGNORE
        assert d.reason == "wake word without command"


class TestSpeakerVerification:
    """Step 2 — fail closed on any unverified speaker."""

    def test_stranger_is_ignored(self):
        verifier = FakeVerifier(is_enrolled=False, confidence=0.42)
        gate = StreamGate(verifier)
        d = gate.decide("hey sovereign, system status", _audio())
        assert d.action == GateAction.IGNORE
        assert d.reason == "unverified speaker"
        assert d.speaker_confidence == pytest.approx(0.42)

    def test_verifier_exception_is_ignored(self):
        gate = StreamGate(FakeVerifier(raises=True))
        d = gate.decide("hey sovereign, system status", _audio())
        assert d.action == GateAction.IGNORE
        assert d.reason == "unverified speaker"
        assert d.speaker_id == "unknown"

    def test_verified_speaker_carries_identity_on_a_command(self):
        gate = StreamGate(FakeVerifier())
        d = gate.decide("hey sovereign, system status", _audio())
        assert d.action == GateAction.COMMAND
        assert d.command_text == "system status"
        assert d.speaker_id == "wilson"
        assert d.speaker_confidence == pytest.approx(0.91)

    def test_unverified_speaker_never_yields_a_command(self):
        gate = StreamGate(FakeVerifier(is_enrolled=False))
        d = gate.decide("hey sovereign, delete the database", _audio())
        assert d.action != GateAction.COMMAND
        assert d.command_text == ""


class TestSleepAndWake:
    def setup_method(self):
        self.gate = StreamGate(FakeVerifier())

    def test_starts_awake(self):
        assert self.gate.asleep is False

    def test_sleep_phrase_puts_the_gate_to_sleep(self):
        d = self.gate.decide("hey sovereign, sleep", _audio())
        assert d.action == GateAction.SLEEP
        assert self.gate.asleep is True

    def test_alternate_sleep_phrase(self):
        d = self.gate.decide("hey sovereign, go to sleep", _audio())
        assert d.action == GateAction.SLEEP
        assert self.gate.asleep is True

    def test_whisper_punctuation_still_sleeps_and_wakes(self):
        # Whisper's real output shape, not the hand-typed one.
        assert self.gate.decide("Hey, Sovereign. Sleep.", _audio()).action == GateAction.SLEEP
        assert self.gate.decide("Hey, Sovereign, wake up.", _audio()).action == GateAction.WAKE

    def test_sleep_is_idempotent(self):
        self.gate.decide("hey sovereign, sleep", _audio())
        d = self.gate.decide("hey sovereign, sleep", _audio())
        assert d.action == GateAction.SLEEP
        assert self.gate.asleep is True

    def test_asleep_ignores_commands(self):
        self.gate.decide("hey sovereign, sleep", _audio())
        d = self.gate.decide("hey sovereign, system status", _audio())
        assert d.action == GateAction.IGNORE
        assert d.reason == "asleep"
        assert d.command_text == ""

    def test_owner_can_wake(self):
        self.gate.decide("hey sovereign, sleep", _audio())
        d = self.gate.decide("hey sovereign, wake up", _audio())
        assert d.action == GateAction.WAKE
        assert self.gate.asleep is False

    def test_bare_wake_phrase(self):
        self.gate.decide("hey sovereign, sleep", _audio())
        d = self.gate.decide("hey sovereign, wake", _audio())
        assert d.action == GateAction.WAKE

    def test_stranger_cannot_wake_a_sleeping_gate(self):
        verifier = FakeVerifier()
        gate = StreamGate(verifier)
        gate.decide("hey sovereign, sleep", _audio())
        assert gate.asleep is True

        # A different voice tries the wake phrase while the gate is asleep.
        verifier.set_enrolled(False)
        d = gate.decide("hey sovereign, wake up", _audio())
        assert d.action == GateAction.IGNORE
        assert d.reason == "unverified speaker"
        assert gate.asleep is True

    def test_stranger_cannot_sleep_the_gate(self):
        gate = StreamGate(FakeVerifier(is_enrolled=False))
        d = gate.decide("hey sovereign, sleep", _audio())
        assert d.action == GateAction.IGNORE
        assert d.reason == "unverified speaker"
        assert gate.asleep is False

    def test_waking_while_awake_is_a_command(self):
        # "wake up" is only a wake phrase while asleep; awake it is just text.
        d = self.gate.decide("hey sovereign, wake up", _audio())
        assert d.action == GateAction.COMMAND
        assert d.command_text == "wake up"

    def test_sleep_substring_is_not_a_sleep_phrase(self):
        # Exact match is deliberate — "sleep through the alarm" must not
        # put the agent to sleep.
        d = self.gate.decide("hey sovereign, sleep through the alarm", _audio())
        assert d.action == GateAction.COMMAND
        assert d.command_text == "sleep through the alarm"
        assert self.gate.asleep is False

    def test_trailing_punctuation_is_normalized(self):
        d = self.gate.decide("hey sovereign, sleep.", _audio())
        assert d.action == GateAction.SLEEP

    def test_case_is_normalized(self):
        d = self.gate.decide("Hey Sovereign, SLEEP!", _audio())
        assert d.action == GateAction.SLEEP


class TestCustomPhrases:
    def test_custom_wake_words(self):
        gate = StreamGate(
            FakeVerifier(),
            wake_words=["computer"],
        )
        assert gate.decide("hey computer, status", _audio()).action == (
            GateAction.COMMAND
        )
        assert gate.decide("hey sovereign, status", _audio()).action == (
            GateAction.IGNORE
        )

    def test_custom_sleep_and_wake_phrases(self):
        gate = StreamGate(
            FakeVerifier(),
            sleep_phrases=("hibernate",),
            wake_phrases=("rise",),
        )
        assert gate.decide("hey sovereign, sleep", _audio()).action == (
            GateAction.COMMAND
        )
        assert gate.decide("hey sovereign, hibernate", _audio()).action == (
            GateAction.SLEEP
        )
        assert gate.decide("hey sovereign, rise", _audio()).action == (
            GateAction.WAKE
        )
