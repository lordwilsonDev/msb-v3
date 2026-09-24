"""Tests for continuous voice stream."""

from __future__ import annotations

import pytest

import msb_v3.speech.stream as stream_module
from msb_v3.speech.gate import GateAction, NotEnrolledError
from msb_v3.speech.models import AudioBuffer, SpeakerIdentity, Transcript
from msb_v3.speech.stream import StreamResult, VoiceStream, VoiceStreamConfig

pytest.importorskip(
    "webrtcvad",
    reason="speech VAD is an EXPERIMENTAL extra: pip install -e '.[speech]'",
)


class FakeVerifier:
    """Duck-typed stand-in for SpeakerVerifier — no resemblyzer, no mic."""

    def __init__(
        self,
        speakers: tuple[str, ...] = ("wilson",),
        is_enrolled: bool = True,
        confidence: float = 0.9,
    ) -> None:
        self._speakers = list(speakers)
        self._is_enrolled = is_enrolled
        self._confidence = confidence
        self.verify_calls = 0

    def list_speakers(self) -> list[str]:
        return list(self._speakers)

    def verify(self, audio: AudioBuffer) -> SpeakerIdentity:
        self.verify_calls += 1
        return SpeakerIdentity(
            speaker_id="wilson" if self._is_enrolled else "unknown",
            confidence=self._confidence,
            is_enrolled=self._is_enrolled,
            method="fake",
        )


def _enrolled(**kwargs) -> FakeVerifier:
    return FakeVerifier(**kwargs)


def _stub_utterance(monkeypatch, text: str) -> None:
    """Replace capture + transcription so the loop runs without a mic."""
    monkeypatch.setattr(
        stream_module,
        "capture_until_silence",
        lambda **kwargs: AudioBuffer(samples=[0.0] * 160, sample_rate=16000),
    )
    monkeypatch.setattr(
        stream_module,
        "transcribe",
        lambda audio, **kwargs: Transcript(text=text, confidence=0.9),
    )


class TestStreamResult:
    def test_has_fields(self):
        r = StreamResult(state="WAITING")
        assert r.state == "WAITING"
        assert r.command_text == ""
        assert r.response_text == ""
        assert r.session is None
        assert r.error == ""
        assert r.latency_ms == 0.0

    def test_has_gate_fields(self):
        r = StreamResult(state="WAITING")
        assert r.action == ""
        assert r.speaker_id == ""
        assert r.speaker_confidence == 0.0

    def test_serializes(self):
        r = StreamResult(state="PROCESSING", command_text="status")
        d = r.as_dict()
        assert d["state"] == "PROCESSING"
        assert d["command_text"] == "status"

    def test_serializes_gate_fields(self):
        r = StreamResult(
            state="RESPONDING",
            action=GateAction.COMMAND.value,
            speaker_id="wilson",
            speaker_confidence=0.9,
        )
        d = r.as_dict()
        assert d["action"] == "COMMAND"
        assert d["speaker_id"] == "wilson"
        assert d["speaker_confidence"] == pytest.approx(0.9)


class TestVoiceStreamConfig:
    def test_defaults(self):
        c = VoiceStreamConfig()
        assert c.whisper_model == "tiny"
        assert c.sample_rate == 16000
        assert c.silence_after == 1.0
        assert c.continuous is True
        assert c.enrollments_path is None

    def test_custom(self):
        c = VoiceStreamConfig(whisper_model="base", continuous=False)
        assert c.whisper_model == "base"
        assert c.continuous is False

    def test_enrollments_path_passthrough(self):
        c = VoiceStreamConfig(enrollments_path="/tmp/enrollments.json")
        assert c.enrollments_path == "/tmp/enrollments.json"


class TestVoiceStreamRequiresEnrollment:
    """Fail-closed start-up: nobody enrolled → refuse before the mic opens."""

    def test_listen_once_raises_when_nobody_enrolled(self):
        stream = VoiceStream(verifier=FakeVerifier(speakers=()))
        with pytest.raises(NotEnrolledError):
            stream.listen_once()

    def test_listen_continuous_raises_when_nobody_enrolled(self):
        stream = VoiceStream(verifier=FakeVerifier(speakers=()))
        with pytest.raises(NotEnrolledError):
            stream.listen_continuous(max_iterations=1)

    def test_default_stream_refuses_without_touching_the_microphone(self):
        # The real SpeakerVerifier has no enrollments on a fresh machine, so
        # the default stream must refuse rather than appear to listen.
        with pytest.raises(NotEnrolledError):
            VoiceStream().listen_once()

    def test_accepts_an_injected_verifier(self):
        verifier = _enrolled()
        assert VoiceStream(verifier=verifier).verifier is verifier


class TestVoiceStreamGate:
    """`listen_once` routes every utterance through the gate."""

    def _stream(self, **verifier_kwargs) -> VoiceStream:
        return VoiceStream(
            config=VoiceStreamConfig(speak_aloud=False),
            verifier=_enrolled(**verifier_kwargs),
        )

    def test_no_wake_word_is_ignored_without_verifying(self, monkeypatch):
        _stub_utterance(monkeypatch, "what is the system status")
        stream = self._stream()
        result = stream.listen_once()
        assert result.action == GateAction.IGNORE.value
        assert result.error == "no wake word"
        assert result.state == "WAITING"
        assert stream.verifier.verify_calls == 0

    def test_unverified_speaker_is_ignored(self, monkeypatch):
        _stub_utterance(monkeypatch, "hey sovereign, system status")
        stream = self._stream(is_enrolled=False)
        result = stream.listen_once()
        assert result.action == GateAction.IGNORE.value
        assert result.error == "unverified speaker"
        assert result.response_text == ""
        assert stream.verifier.verify_calls == 1

    def test_command_is_processed(self, monkeypatch):
        _stub_utterance(monkeypatch, "hey sovereign, system status")
        stream = self._stream()
        result = stream.listen_once()
        assert result.action == GateAction.COMMAND.value
        assert result.state == "RESPONDING"
        assert result.command_text == "system status"
        assert result.response_text
        assert result.speaker_id == "wilson"
        assert result.speaker_confidence == pytest.approx(0.9)
        assert result.session is not None

    def test_sleep_sets_the_stream_asleep(self, monkeypatch):
        _stub_utterance(monkeypatch, "hey sovereign, sleep")
        stream = self._stream()
        result = stream.listen_once()
        assert result.action == GateAction.SLEEP.value
        assert stream.asleep is True
        assert result.state == "WAITING"

    def test_asleep_ignores_commands(self, monkeypatch):
        _stub_utterance(monkeypatch, "hey sovereign, sleep")
        stream = self._stream()
        stream.listen_once()

        _stub_utterance(monkeypatch, "hey sovereign, system status")
        result = stream.listen_once()
        assert result.action == GateAction.IGNORE.value
        assert result.error == "asleep"
        assert stream.asleep is True

    def test_wake_returns_the_stream_to_service(self, monkeypatch):
        _stub_utterance(monkeypatch, "hey sovereign, sleep")
        stream = self._stream()
        stream.listen_once()
        assert stream.asleep is True

        _stub_utterance(monkeypatch, "hey sovereign, wake up")
        result = stream.listen_once()
        assert result.action == GateAction.WAKE.value
        assert stream.asleep is False

        _stub_utterance(monkeypatch, "hey sovereign, system status")
        result = stream.listen_once()
        assert result.action == GateAction.COMMAND.value

    def test_speak_aloud_announces_sleep(self, monkeypatch):
        _stub_utterance(monkeypatch, "hey sovereign, sleep")
        stream = VoiceStream(verifier=_enrolled())
        spoken: list[str] = []
        monkeypatch.setattr(
            stream.responder,
            "_render_response",
            lambda text: spoken.append(text) or True,
        )
        stream.listen_once()
        assert spoken == ["Going to sleep."]

    def test_speak_aloud_announces_wake(self, monkeypatch):
        _stub_utterance(monkeypatch, "hey sovereign, sleep")
        stream = VoiceStream(verifier=_enrolled())
        stream.listen_once()

        spoken: list[str] = []
        monkeypatch.setattr(
            stream.responder,
            "_render_response",
            lambda text: spoken.append(text) or True,
        )
        _stub_utterance(monkeypatch, "hey sovereign, wake up")
        stream.listen_once()
        assert spoken == ["I'm awake."]


class TestVoiceStream:
    def setup_method(self):
        # Always-on mode refuses to start without an enrollment, so the
        # mic-touching tests run behind an enrolled fake verifier.
        self.stream = VoiceStream(verifier=_enrolled())

    def test_initial_state(self):
        assert not self.stream.is_running
        assert self.stream.asleep is False

    def test_stop(self):
        self.stream.stop()
        assert not self.stream.is_running

    def test_listen_once_returns_result(self):
        # This will capture silence and return WAITING/error
        result = self.stream.listen_once()
        assert isinstance(result, StreamResult)
        assert result.state in ("WAITING", "LISTENING", "PROCESSING", "RESPONDING")

    def test_listen_once_has_latency(self):
        result = self.stream.listen_once()
        assert result.latency_ms >= 0

    def test_listen_continuous_limit(self):
        # Test with max_iterations=1 to avoid long wait
        results = self.stream.listen_continuous(max_iterations=1)
        assert len(results) <= 1

    def test_stream_stops(self):
        self.stream._running = True
        self.stream.stop()
        assert not self.stream.is_running

    def test_config_passthrough(self):
        config = VoiceStreamConfig(whisper_model="base")
        stream = VoiceStream(config, verifier=_enrolled())
        assert stream.config.whisper_model == "base"
