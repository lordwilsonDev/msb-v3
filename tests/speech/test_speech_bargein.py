"""Tests for barge-in support."""

from __future__ import annotations

from msb_v3.speech.bargein import BargeInConfig, BargeInController, TTSInterrupter


class TestBargeInConfig:
    def test_defaults(self):
        c = BargeInConfig()
        assert c.enabled is True
        assert c.check_interval_ms == 100.0
        assert c.speech_threshold == 0.3
        assert c.min_speech_frames == 3


class TestBargeInController:
    def setup_method(self):
        self.controller = BargeInController()

    def test_initial_state(self):
        assert not self.controller.is_monitoring
        assert not self.controller.should_interrupt()

    def test_reset(self):
        self.controller._interrupted = True
        self.controller.reset()
        assert not self.controller.should_interrupt()

    def test_disabled_config(self):
        config = BargeInConfig(enabled=False)
        controller = BargeInController(config)
        controller.start_monitoring()
        # Should not actually start monitoring
        assert not controller.is_monitoring

    def test_monitoring_lifecycle(self):
        controller = BargeInController(BargeInConfig(enabled=False))
        controller.start_monitoring()
        controller.stop_monitoring()
        # No crash

    def test_should_interrupt_false_by_default(self):
        assert self.controller.should_interrupt() is False

    def test_reset_clears_state(self):
        self.controller._interrupted = True
        self.controller._speech_count = 10
        self.controller._total_frames = 10
        self.controller.reset()
        assert self.controller._speech_count == 0
        assert self.controller._total_frames == 0


class TestTTSInterrupter:
    def setup_method(self):
        self.interrupter = TTSInterrupter()

    def test_initial_state(self):
        assert not self.interrupter.was_interrupted

    def test_speak_returns_bool(self):
        # Test with empty string (no-op)
        result = self.interrupter.speak_with_barge_in("")
        assert isinstance(result, bool)
