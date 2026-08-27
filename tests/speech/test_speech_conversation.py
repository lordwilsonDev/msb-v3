"""Tests for conversational state management."""

from __future__ import annotations

from msb_v3.speech.conversation import (
    ConversationContext,
    ConversationManager,
    ConversationTurn,
)


class TestConversationTurn:
    def test_has_fields(self):
        turn = ConversationTurn(speaker="user", text="hello")
        assert turn.speaker == "user"
        assert turn.text == "hello"
        assert turn.timestamp  # auto-generated

    def test_serializes(self):
        turn = ConversationTurn(speaker="system", text="hi", intent="greet")
        d = turn.as_dict()
        assert d["speaker"] == "system"
        assert d["intent"] == "greet"


class TestConversationContext:
    def test_defaults(self):
        ctx = ConversationContext()
        assert ctx.topic == ""
        assert ctx.turn_count == 0
        assert ctx.pending_clarification is False

    def test_serializes(self):
        ctx = ConversationContext(topic="AI", turn_count=2)
        d = ctx.as_dict()
        assert d["topic"] == "AI"
        assert d["turn_count"] == 2


class TestConversationManager:
    def setup_method(self):
        self.manager = ConversationManager()

    def test_initial_state(self):
        ctx = self.manager.get_context()
        assert ctx.turn_count == 0

    def test_add_turn(self):
        self.manager.add_turn("user", "Research AI")
        history = self.manager.get_history()
        assert len(history) == 1
        assert history[0].speaker == "user"

    def test_context_updates(self):
        self.manager.add_turn("user", "Research AI inference")
        ctx = self.manager.get_context()
        assert ctx.turn_count == 1
        assert "AI inference" in ctx.topic

    def test_follow_up_detection(self):
        self.manager.add_turn("user", "Research BitNet")
        assert self.manager.is_follow_up("What about training?")
        assert self.manager.is_follow_up("And efficiency?")
        assert self.manager.is_follow_up("Yes")
        assert self.manager.is_follow_up("No")

    def test_not_follow_up(self):
        assert not self.manager.is_follow_up("Research the latest developments in local AI inference")

    def test_resolve_follow_up(self):
        self.manager.add_turn("user", "Research BitNet")
        resolved = self.manager.resolve_follow_up("training efficiency")
        assert "BitNet" in resolved
        assert "training efficiency" in resolved

    def test_resolve_not_follow_up(self):
        resolved = self.manager.resolve_follow_up("Research something new")
        assert resolved == "Research something new"

    def test_max_turns(self):
        manager = ConversationManager(max_turns=3)
        for i in range(5):
            manager.add_turn("user", f"message {i}")
        history = manager.get_history()
        assert len(history) == 3
        assert history[0].text == "message 2"

    def test_reset(self):
        self.manager.add_turn("user", "hello")
        self.manager.reset()
        assert self.manager.get_context().turn_count == 0
        assert len(self.manager.get_history()) == 0

    def test_multi_turn_conversation(self):
        # Simulate a multi-turn conversation
        self.manager.add_turn("user", "Research BitNet")
        self.manager.add_turn("system", "Sure. What aspect?")

        ctx = self.manager.get_context()
        assert ctx.pending_clarification is True

        self.manager.add_turn("user", "Training efficiency")
        ctx = self.manager.get_context()
        assert ctx.pending_clarification is False
        assert "BitNet" in ctx.topic

    def test_clarification_detection(self):
        self.manager.add_turn("user", "What is the status?")
        ctx = self.manager.get_context()
        assert ctx.pending_clarification is True

    def test_get_history_limit(self):
        for i in range(10):
            self.manager.add_turn("user", f"msg {i}")
        history = self.manager.get_history(n_turns=3)
        assert len(history) == 3

    def test_turn_with_intent(self):
        self.manager.add_turn(
            "user", "Deploy canary", intent="deploy", endpoint="/governance/execute"
        )
        ctx = self.manager.get_context()
        assert ctx.last_intent == "deploy"
        assert ctx.last_endpoint == "/governance/execute"
