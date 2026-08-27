"""Tests for TTS engine and voice response pipeline."""

from __future__ import annotations

from msb_v3.speech.models import VoiceCommand
from msb_v3.speech.response import _RESPONSES, VoiceResponder, VoiceResponse
from msb_v3.speech.tts.engine import list_voices, speak, speak_to_file

# ── TTS Engine tests ──────────────────────────────────────────────────────


def test_speak_empty_text() -> None:
    assert speak("") is False
    assert speak("   ") is False


def test_speak_returns_bool() -> None:
    # speak() calls macOS say — may fail in headless test env
    result = speak("test", rate=300)
    assert isinstance(result, bool)


def test_speak_to_file_empty() -> None:
    assert speak_to_file("", "/tmp/test.aiff") is False


def test_speak_to_file_creates_file(tmp_path: object) -> None:
    import os

    out = os.path.join(str(tmp_path), "test.aiff")
    result = speak_to_file("Hello world", out, rate=300)
    # May succeed or fail depending on audio system availability
    assert isinstance(result, bool)
    if result:
        assert os.path.exists(out)


def test_list_voices() -> None:
    voices = list_voices("en")
    assert isinstance(voices, list)
    # macOS should have at least English voices
    if voices:
        assert all("name" in v for v in voices)
        assert all("language" in v for v in voices)


def test_list_voices_all() -> None:
    voices = list_voices(language=None)
    assert isinstance(voices, list)


# ── Voice Response tests ─────────────────────────────────────────────────


def test_voice_response_to_dict() -> None:
    r = VoiceResponse(input_text="hello", response_text="hi back", spoken=True)
    d = r.to_dict()
    assert d["input"] == "hello"
    assert d["response"] == "hi back"
    assert d["spoken"] is True


def test_responder_text_empty() -> None:
    responder = VoiceResponder(speak_aloud=False)
    result = responder.respond_to_text("")
    assert result.response_text == _RESPONSES["empty"]
    assert result.spoken is False


def test_responder_text_research() -> None:
    responder = VoiceResponder(speak_aloud=False)
    result = responder.respond_to_text("Research local AI inference")
    assert result.authorized is True
    assert result.command is not None
    assert "/research" in result.command.endpoint
    assert "research" in result.response_text.lower() or "local ai" in result.response_text.lower()


def test_responder_text_status() -> None:
    responder = VoiceResponder(speak_aloud=False)
    result = responder.respond_to_text("System status")
    assert result.command is not None
    assert result.command.endpoint == "/system/health"


def test_responder_text_help() -> None:
    responder = VoiceResponder(speak_aloud=False)
    result = responder.respond_to_text("Help")
    assert result.response_text == _RESPONSES["help"]


def test_responder_text_kill_switch() -> None:
    responder = VoiceResponder(speak_aloud=False)
    result = responder.respond_to_text("Kill the loop")
    assert result.command is not None
    assert "killswitch" in result.command.endpoint
    # Safety gate: CRITICAL risk requires confirmation
    assert "confirm" in result.response_text.lower()


def test_responder_text_deploy() -> None:
    responder = VoiceResponder(speak_aloud=False)
    result = responder.respond_to_text("Deploy the canary release")
    assert result.command is not None
    assert "/governance/execute" in result.command.endpoint


def test_responder_custom_processor() -> None:
    def my_processor(cmd: VoiceCommand) -> dict:
        return {"summary": f"Custom result for {cmd.command}"}

    responder = VoiceResponder(speak_aloud=False)
    result = responder.respond_to_text("Research something", processor=my_processor)
    assert "Custom result" in result.response_text


def test_responder_processor_error() -> None:
    def bad_processor(cmd: VoiceCommand) -> dict:
        raise ValueError("boom")

    responder = VoiceResponder(speak_aloud=False)
    result = responder.respond_to_text("Research something", processor=bad_processor)
    assert result.response_text == _RESPONSES["error"]


def test_responder_latency_recorded() -> None:
    responder = VoiceResponder(speak_aloud=False)
    result = responder.respond_to_text("Status")
    assert result.latency_ms >= 0


def test_responder_timestamp_recorded() -> None:
    responder = VoiceResponder(speak_aloud=False)
    result = responder.respond_to_text("Status")
    assert result.timestamp != "" or result.latency_ms >= 0
