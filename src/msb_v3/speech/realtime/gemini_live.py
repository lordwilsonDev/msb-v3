"""Gemini Live adapter (WebSocket via google-genai)."""

from __future__ import annotations

from typing import Any, AsyncContextManager, AsyncIterator, Callable, Optional

from msb_v3.speech.realtime.base import (
    EventKind,
    ProviderError,
    RealtimeConfig,
    RealtimeEvent,
    safe_reason,
)
from msb_v3.speech.realtime.tools import ToolRegistry

_MIME = "audio/pcm;rate=16000"


class GeminiLiveProvider:
    name = "gemini"

    def __init__(
        self,
        config: RealtimeConfig,
        api_key: str,
        tools: Optional[ToolRegistry] = None,
        session_factory: Optional[Callable[[], AsyncContextManager[Any]]] = None,
    ) -> None:
        self.config = config
        self._api_key = api_key
        self.tools = tools
        self._factory = session_factory or self._default_factory
        self._cm: Optional[AsyncContextManager[Any]] = None
        self._session: Any = None
        self._in_turn = False
        self._dropping = False

    def _live_config(self) -> Any:
        from google.genai import types

        speech = None
        if self.config.voice:
            speech = types.SpeechConfig(voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=self.config.voice)))
        tools: Optional[list[Any]] = None
        if self.tools and self.tools.gemini_declarations():
            tools = [types.Tool(function_declarations=[
                types.FunctionDeclaration(**d) for d in self.tools.gemini_declarations()])]
        return types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO],
            system_instruction=self.config.instructions,
            speech_config=speech,
            tools=tools,
            output_audio_transcription=types.AudioTranscriptionConfig(),
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(disabled=True)),
        )

    def _default_factory(self) -> AsyncContextManager[Any]:
        from google import genai

        client = genai.Client(api_key=self._api_key)
        return client.aio.live.connect(model=self.config.model, config=self._live_config())

    async def connect(self) -> None:
        self._cm = self._factory()
        try:
            self._session = await self._cm.__aenter__()
        except Exception as exc:  # noqa: BLE001 — normalize, never echo SDK text (may carry secrets)
            self._session = None
            raise ProviderError(
                f"gemini live connect failed ({safe_reason(exc, self._api_key)})"
            ) from None

    async def send_audio(self, pcm16_16k: bytes) -> None:
        from google.genai import types

        if not self._in_turn:
            await self._session.send_realtime_input(activity_start=types.ActivityStart())
            self._in_turn = True
            self._dropping = False
        await self._session.send_realtime_input(audio=types.Blob(data=pcm16_16k, mime_type=_MIME))

    async def end_turn(self) -> None:
        from google.genai import types

        await self._session.send_realtime_input(activity_end=types.ActivityEnd())
        self._in_turn = False

    async def interrupt(self) -> None:
        # Manual activity mode has no cancel call: drop the rest of this
        # reply locally; the next activity_start interrupts server-side.
        self._dropping = True

    async def send_tool_result(self, call_id: str, name: str, output: dict) -> None:
        from google.genai import types

        await self._session.send_tool_response(
            function_responses=[types.FunctionResponse(id=call_id, name=name, response=output)])

    async def events(self) -> AsyncIterator[RealtimeEvent]:
        try:
            async for ev in self._raw_events():
                yield ev
        except Exception as exc:  # noqa: BLE001 — SDK stream errors become ProviderError, key-free
            raise ProviderError(
                f"gemini live stream failed ({safe_reason(exc, self._api_key)})"
            ) from None

    async def _raw_events(self) -> AsyncIterator[RealtimeEvent]:
        # receive() ends after each turn_complete, so keep re-entering it;
        # the caller stops iterating when it has the turn it wants.
        while True:
            async for msg in self._session.receive():
                call = getattr(msg, "tool_call", None)
                if call is not None:
                    for fc in call.function_calls or []:
                        yield RealtimeEvent(EventKind.TOOL_CALL, call_id=fc.id or "",
                                            name=fc.name or "", arguments=dict(fc.args or {}))
                content = getattr(msg, "server_content", None)
                if content is None:
                    continue
                if content.model_turn is not None and not self._dropping:
                    for part in content.model_turn.parts or []:
                        data = getattr(getattr(part, "inline_data", None), "data", None)
                        if data:
                            yield RealtimeEvent(EventKind.AUDIO, audio=data)
                if content.output_transcription is not None and content.output_transcription.text:
                    yield RealtimeEvent(EventKind.TRANSCRIPT, text=content.output_transcription.text)
                if content.turn_complete or content.interrupted:
                    self._dropping = False
                    yield RealtimeEvent(EventKind.TURN_DONE)

    async def close(self) -> None:
        if self._cm is not None:
            try:
                await self._cm.__aexit__(None, None, None)
            finally:
                self._cm, self._session, self._in_turn = None, None, False
