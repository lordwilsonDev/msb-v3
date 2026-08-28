from __future__ import annotations

import pytest

from msb_v3.speech.audio import (
    AudioDeviceError,
    AudioPayload,
    AudioRendererRegistry,
    RenderContext,
    RenderResult,
    RenderStatus,
    UnavailableAudioRenderer,
)
from msb_v3.speech.response import VoiceResponder


class FakeRenderer:
    capabilities = frozenset({"local_playback"})

    def __init__(self, renderer_id: str) -> None:
        self.renderer_id = renderer_id
        self.calls = 0

    def available(self) -> bool:
        return True

    def unavailable_reason(self) -> str:
        return ""

    def render(self, audio: AudioPayload, context: RenderContext) -> RenderResult:
        audio.validate()
        self.calls += 1
        return RenderResult(RenderStatus.PLAYED, self.renderer_id)


def test_payload_rejects_misaligned_pcm() -> None:
    with pytest.raises(Exception):
        AudioPayload(b"x").validate()


def test_registry_selects_first_available_provider() -> None:
    first = FakeRenderer("first")
    second = FakeRenderer("second")
    registry = AudioRendererRegistry([first, second])
    result = registry.render(AudioPayload(b""), RenderContext(), frozenset({"local_playback"}))
    assert result.renderer_id == "first"
    assert first.calls == 1
    assert second.calls == 0


def test_registry_swap_requires_no_consumer_change() -> None:
    first = FakeRenderer("first")
    second = FakeRenderer("second")
    registry = AudioRendererRegistry([first])
    assert registry.render(AudioPayload(b""), RenderContext()).renderer_id == "first"
    registry.register(second)
    assert registry.render(AudioPayload(b""), RenderContext()).renderer_id == "first"
    first.available = lambda: False  # type: ignore[method-assign]
    assert registry.render(AudioPayload(b""), RenderContext()).renderer_id == "second"


def test_registry_fails_closed_when_no_renderer_is_available() -> None:
    registry = AudioRendererRegistry([UnavailableAudioRenderer()])
    result = registry.render(AudioPayload(b""), RenderContext())
    assert result.status == RenderStatus.SAFE_FAILURE
    assert result.error_class == "AUDIO_DEVICE_UNAVAILABLE"
    with pytest.raises(AudioDeviceError):
        registry.select()


def test_responder_accepts_injected_audio_registry() -> None:
    renderer = FakeRenderer("test-renderer")
    responder = VoiceResponder(
        speak_aloud=True,
        audio_registry=AudioRendererRegistry([renderer]),
    )
    result = responder.respond_to_text("help")
    assert result.spoken is True
    assert renderer.calls == 1
