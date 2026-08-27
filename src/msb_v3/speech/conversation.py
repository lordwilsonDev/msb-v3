"""Conversational state — multi-turn dialogue with context tracking.

Tracks conversation history and context for follow-up commands.
Enables multi-turn interactions like:

    User: "Research BitNet"
    System: "Sure. What aspect?"
    User: "Training efficiency"
    System: "Starting research on BitNet training efficiency."

Usage::

    from msb_v3.speech.conversation import ConversationManager

    manager = ConversationManager()
    context = manager.get_context()
    # ... process command with context ...
    manager.add_turn(user="Research BitNet", system="Sure. What aspect?")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class ConversationTurn:
    """A single turn in the conversation."""

    speaker: str  # "user" or "system"
    text: str
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    intent: Optional[str] = None
    endpoint: Optional[str] = None
    params: Optional[Dict[str, Any]] = None

    def as_dict(self) -> dict:
        return {
            "speaker": self.speaker,
            "text": self.text,
            "timestamp": self.timestamp,
            "intent": self.intent,
            "endpoint": self.endpoint,
            "params": self.params,
        }


@dataclass
class ConversationContext:
    """Context extracted from conversation history."""

    topic: str = ""
    last_intent: str = ""
    last_endpoint: str = ""
    last_params: Optional[Dict[str, Any]] = None
    turn_count: int = 0
    pending_clarification: bool = False
    clarification_topic: str = ""

    def as_dict(self) -> dict:
        return {
            "topic": self.topic,
            "last_intent": self.last_intent,
            "last_endpoint": self.last_endpoint,
            "last_params": self.last_params,
            "turn_count": self.turn_count,
            "pending_clarification": self.pending_clarification,
            "clarification_topic": self.clarification_topic,
        }


class ConversationManager:
    """Manages multi-turn conversation state.

    Tracks:
    - Full conversation history
    - Current topic/context
    - Pending clarifications
    - Follow-up detection
    """

    def __init__(self, max_turns: int = 20) -> None:
        self.max_turns = max_turns
        self._turns: List[ConversationTurn] = []
        self._context = ConversationContext()

    def add_turn(
        self,
        speaker: str,
        text: str,
        intent: Optional[str] = None,
        endpoint: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Add a turn to the conversation."""
        turn = ConversationTurn(
            speaker=speaker,
            text=text,
            intent=intent,
            endpoint=endpoint,
            params=params,
        )
        self._turns.append(turn)

        # Trim old turns
        if len(self._turns) > self.max_turns:
            self._turns = self._turns[-self.max_turns:]

        # Update context
        self._update_context(turn)

    def get_context(self) -> ConversationContext:
        """Get the current conversation context."""
        return self._context

    def get_history(self, n_turns: int = 5) -> List[ConversationTurn]:
        """Get the last n turns."""
        return self._turns[-n_turns:]

    def is_follow_up(self, text: str) -> bool:
        """Check if a message is likely a follow-up to the previous turn.

        Follow-ups are typically short references like:
        - "What about X?"
        - "And Y?"
        - "Yes" / "No"
        - "The first one"
        """
        text_lower = text.lower().strip()

        # Short responses
        if text_lower in ("yes", "no", "yeah", "nope", "ok", "okay", "sure"):
            return True

        # Follow-up patterns
        follow_up_patterns = [
            "what about",
            "and ",
            "also ",
            "the first",
            "the second",
            "the third",
            "that one",
            "this one",
            "which one",
            "more about",
            "tell me more",
            "elaborate",
            "explain",
        ]

        if any(p in text_lower for p in follow_up_patterns):
            return True

        # Very short messages are likely follow-ups
        if len(text.split()) <= 3 and self._turns:
            return True

        return False

    def resolve_follow_up(self, text: str) -> str:
        """Resolve a follow-up message by combining with context.

        If the user says "training efficiency" after "Research BitNet",
        this returns "BitNet training efficiency".
        """
        if not self.is_follow_up(text):
            return text

        context = self._context

        # If we have a topic, prepend it
        if context.topic:
            # Check if the follow-up is a refinement
            if any(
                w in text.lower()
                for w in ["what", "how", "why", "when", "where", "who"]
            ):
                return f"{context.topic} {text}"
            # Otherwise, append to topic
            return f"{context.topic} {text}"

        return text

    def reset(self) -> None:
        """Reset the conversation."""
        self._turns.clear()
        self._context = ConversationContext()

    def _update_context(self, turn: ConversationTurn) -> None:
        """Update context based on new turn."""
        self._context.turn_count = len(self._turns)

        if turn.speaker == "user":
            # Extract topic from user message
            text = turn.text.lower()

            # Simple topic extraction
            for prefix in ["research ", "find ", "search ", "look up ", "tell me about "]:
                if text.startswith(prefix):
                    self._context.topic = turn.text[len(prefix) :].strip()
                    break
            else:
                # If not a new topic, it's likely a follow-up
                if self.is_follow_up(turn.text) and self._context.topic:
                    # Keep the existing topic
                    pass
                else:
                    self._context.topic = turn.text

            if turn.intent:
                self._context.last_intent = turn.intent
            if turn.endpoint:
                self._context.last_endpoint = turn.endpoint
            if turn.params:
                self._context.last_params = turn.params

            # Check for clarification patterns
            if "?" in turn.text:
                self._context.pending_clarification = True
                self._context.clarification_topic = turn.text
            else:
                self._context.pending_clarification = False

        elif turn.speaker == "system":
            # System responses with questions indicate clarification needed
            if "?" in turn.text:
                self._context.pending_clarification = True
            else:
                self._context.pending_clarification = False
