"""OpenAI Realtime adapter (WebSocket via the openai SDK)."""

from __future__ import annotations

import base64
import json
from typing import Any, AsyncContextManager, AsyncIterator, Callable, Optional

from msb_v3.speech.realtime.audio import MIC_RATE, resample_pcm16
from msb_v3.speech.realtime.base import (
    EventKind,
    ProviderError,
    RealtimeConfig,
    RealtimeEvent,
    safe_reason,
)
from msb_v3.speech.realtime.tools import ToolRegistry

_OPENAI_IN_RATE = 24000


class OpenAIRealtimeProvider:
    name = "openai"

    def __init__(
        self,
        config: RealtimeConfig,
        api_key: str,
        tools: Optional[ToolRegistry] = None,
        connection_factory: Optional[Callable[[], AsyncContextManager[Any]]] = None,
    ) -> None:
        self.config = config
        self._api_key = api_key
        self.tools = tools
        self._factory = connection_factory or self._default_factory
        self._cm: Optional[AsyncContextManager[Any]] = None
        self._conn: Any = None

    def _default_factory(self) -> AsyncContextManager[Any]:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=self._api_key)
        return client.realtime.connect(model=self.config.model)

    def _session(self) -> dict:
        output: dict = {"format": {"type": "audio/pcm", "rate": 24000}}
        if self.config.voice:
            output["voice"] = self.config.voice
        return {
            "type": "realtime",
            "model": self.config.model,
            "instructions": self.config.instructions,
            "output_modalities": ["audio"],
            "audio": {
                "input": {"format": {"type": "audio/pcm", "rate": _OPENAI_IN_RATE},
                          "turn_detection": None},
                "output": output,
            },
            "tools": self.tools.openai_tools() if self.tools else [],
        }

    async def connect(self) -> None:
        self._cm = self._factory()
        try:
            self._conn = await self._cm.__aenter__()
            await self._conn.session.update(session=self._session())
        except Exception as exc:  # noqa: BLE001 — normalize, never echo SDK text (may carry secrets)
            self._conn = None
            raise ProviderError(
                f"openai realtime connect failed ({safe_reason(exc, self._api_key)})"
            ) from None

    async def send_audio(self, pcm16_16k: bytes) -> None:
        data = resample_pcm16(pcm16_16k, MIC_RATE, _OPENAI_IN_RATE)
        await self._conn.input_audio_buffer.append(audio=base64.b64encode(data).decode("ascii"))

    async def end_turn(self) -> None:
        await self._conn.input_audio_buffer.commit()
        await self._conn.response.create()

    async def interrupt(self) -> None:
        await self._conn.response.cancel()

    async def send_tool_result(self, call_id: str, name: str, output: dict) -> None:
        await self._conn.conversation.item.create(
            item={"type": "function_call_output", "call_id": call_id, "output": json.dumps(output)}
        )
        await self._conn.response.create()

    async def events(self) -> AsyncIterator[RealtimeEvent]:
        try:
            async for ev in self._raw_events():
                yield ev
        except Exception as exc:  # noqa: BLE001 — SDK stream errors become ProviderError, key-free
            raise ProviderError(
                f"openai realtime stream failed ({safe_reason(exc, self._api_key)})"
            ) from None

    async def _raw_events(self) -> AsyncIterator[RealtimeEvent]:
        async for ev in self._conn:
            kind = getattr(ev, "type", "")
            if kind == "response.output_audio.delta":
                yield RealtimeEvent(EventKind.AUDIO, audio=base64.b64decode(ev.delta))
            elif kind == "response.output_audio_transcript.delta":
                yield RealtimeEvent(EventKind.TRANSCRIPT, text=ev.delta)
            elif kind == "response.function_call_arguments.done":
                try:
                    args = json.loads(ev.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                yield RealtimeEvent(EventKind.TOOL_CALL, call_id=ev.call_id, name=ev.name,
                                    arguments=args)
            elif kind == "response.done":
                yield RealtimeEvent(EventKind.TURN_DONE)
            elif kind == "error":
                message = getattr(getattr(ev, "error", None), "message", "") or "provider error"
                yield RealtimeEvent(EventKind.ERROR, text=message)

    async def close(self) -> None:
        if self._cm is not None:
            try:
                await self._cm.__aexit__(None, None, None)
            finally:
                self._cm, self._conn = None, None
