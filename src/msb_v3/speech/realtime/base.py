"""Provider-neutral contract for realtime speech-to-speech sessions."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import AsyncIterator, Dict, Optional, Protocol

DEFAULT_MODELS: Dict[str, str] = {
    "openai": "gpt-realtime-2.1",
    "gemini": "gemini-3.1-flash-live-preview",
}

DEFAULT_INSTRUCTIONS = (
    "You are Sovereign, a concise voice assistant for a busy operator. "
    "Answer in one or two short spoken sentences. Use a tool when one fits; "
    "never claim to have done something no tool did."
)


class ProviderError(RuntimeError):
    """Connection or protocol failure talking to a provider."""


_ERROR_CODES = re.compile(
    r"\b(invalid_api_key|insufficient_quota|model_not_found|rate_limit_exceeded|"
    r"permission_denied|unauthorized|quota_exceeded|PERMISSION_DENIED|UNAUTHENTICATED|"
    r"RESOURCE_EXHAUSTED|NOT_FOUND|INVALID_ARGUMENT|40[13]|404|429)\b"
)


def safe_reason(exc: BaseException, secret: str) -> str:
    """Name the failure without echoing SDK text: exception type + known error code."""
    text = str(exc)
    if secret:
        text = text.replace(secret, "<redacted>")
    match = _ERROR_CODES.search(text)
    return f"{type(exc).__name__}: {match.group(1)}" if match else type(exc).__name__


class EventKind(str, Enum):
    AUDIO = "audio"
    TRANSCRIPT = "transcript"
    TOOL_CALL = "tool_call"
    TURN_DONE = "turn_done"
    ERROR = "error"


@dataclass
class RealtimeEvent:
    kind: EventKind
    audio: bytes = b""  # PCM16 24 kHz for AUDIO
    text: str = ""  # TRANSCRIPT text, or ERROR message
    call_id: str = ""
    name: str = ""
    arguments: dict = field(default_factory=dict)


@dataclass
class RealtimeConfig:
    provider: str
    model: str
    instructions: str = DEFAULT_INSTRUCTIONS
    voice: Optional[str] = None
    session_cap_seconds: float = 300.0

    @classmethod
    def for_provider(cls, provider: str, model: Optional[str] = None) -> "RealtimeConfig":
        return cls(provider=provider, model=model or DEFAULT_MODELS[provider])


class RealtimeProvider(Protocol):
    name: str

    async def connect(self) -> None: ...
    async def send_audio(self, pcm16_16k: bytes) -> None: ...
    async def end_turn(self) -> None: ...
    async def interrupt(self) -> None: ...
    async def send_tool_result(self, call_id: str, name: str, output: dict) -> None: ...
    def events(self) -> AsyncIterator[RealtimeEvent]: ...
    async def close(self) -> None: ...
