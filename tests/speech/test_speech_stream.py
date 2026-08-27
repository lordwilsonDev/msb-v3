"""Tests for continuous voice stream."""

from __future__ import annotations

from msb_v3.speech.stream import StreamResult, VoiceStream, VoiceStreamConfig


class TestStreamResult:
    def test_has_fields(self):
        r = StreamResult(state="WAITING")
        assert r.state == "WAITING"
        assert r.command_text == ""
        assert r.response_text == ""
        assert r.session is None
        assert r.error == ""
        assert r.latency_ms == 0.0

    def test_serializes(self):
        r = StreamResult(state="PROCESSING", command_text="status")
        d = r.as_dict()
        assert d["state"] == "PROCESSING"
        assert d["command_text"] == "status"


class TestVoiceStreamConfig:
    def test_defaults(self):
        c = VoiceStreamConfig()
        assert c.whisper_model == "tiny"
        assert c.sample_rate == 16000
        assert c.silence_after == 1.0
        assert c.continuous is True

    def test_custom(self):
        c = VoiceStreamConfig(whisper_model="base", continuous=False)
        assert c.whisper_model == "base"
        assert c.continuous is False


class TestVoiceStream:
    def setup_method(self):
        self.stream = VoiceStream()

    def test_initial_state(self):
        assert not self.stream.is_running

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
        stream = VoiceStream(config)
        assert stream.config.whisper_model == "base"
