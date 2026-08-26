"""Tests for intent extraction from speech transcripts."""

from __future__ import annotations

from msb_v3.speech.intent import extract_intent, list_commands
from msb_v3.speech.models import Transcript


def test_research_command() -> None:
    t = Transcript(text="Research the latest developments in local AI inference")
    cmd = extract_intent(t)
    assert cmd.endpoint == "/research/assistant/run"
    assert cmd.method == "POST"
    assert "local AI inference" in cmd.params.get("topic", "")
    assert cmd.confidence > 0.5


def test_chat_query_command() -> None:
    t = Transcript(text="What is the system status today")
    cmd = extract_intent(t)
    assert cmd.endpoint == "/chat"
    assert cmd.method == "POST"
    assert "system status" in cmd.params.get("query", "")


def test_deploy_command() -> None:
    t = Transcript(text="Deploy the canary release")
    cmd = extract_intent(t)
    assert cmd.endpoint == "/governance/execute"
    assert cmd.params.get("action") == "deploy_canary"


def test_status_command() -> None:
    t = Transcript(text="System status")
    cmd = extract_intent(t)
    assert cmd.endpoint == "/system/health"
    assert cmd.method == "GET"


def test_kill_switch_command() -> None:
    t = Transcript(text="Kill the loop")
    cmd = extract_intent(t)
    assert cmd.endpoint == "/governance/killswitch/arm"
    assert cmd.params.get("operator") == "voice"


def test_resume_command() -> None:
    t = Transcript(text="Resume the system")
    cmd = extract_intent(t)
    assert cmd.endpoint == "/governance/killswitch/disarm"


def test_flywheel_command() -> None:
    t = Transcript(text="Start flywheel research")
    cmd = extract_intent(t)
    assert cmd.endpoint == "/flywheel/turns"


def test_help_command() -> None:
    t = Transcript(text="Help")
    cmd = extract_intent(t)
    assert cmd.command == "help"
    assert cmd.endpoint is None or cmd.endpoint == ""


def test_fallback_to_chat() -> None:
    t = Transcript(text="I wonder about the weather in Chicago")
    cmd = extract_intent(t)
    assert cmd.endpoint == "/chat"
    assert cmd.params.get("query") == "I wonder about the weather in Chicago"
    assert cmd.confidence == 0.5


def test_empty_transcript() -> None:
    t = Transcript(text="")
    cmd = extract_intent(t)
    assert cmd.command == "empty"
    assert cmd.confidence == 0.0


def test_list_commands_returns_non_empty() -> None:
    commands = list_commands()
    assert len(commands) > 0
    assert all("endpoint" in c for c in commands)
    assert all("method" in c for c in commands)


def test_case_insensitive() -> None:
    t = Transcript(text="RESEARCH quantum computing")
    cmd = extract_intent(t)
    assert cmd.endpoint == "/research/assistant/run"
