from __future__ import annotations

import base64
import json
from types import SimpleNamespace as NS

import pytest

from msb_v3.speech.realtime.base import EventKind, ProviderError, RealtimeConfig
from msb_v3.speech.realtime.openai_rt import OpenAIRealtimeProvider
from msb_v3.speech.realtime.tools import default_tools

KEY = "sk-test-SECRET-OPENAI"


class _Recorder:
    def __init__(self, log, prefix):
        self._log, self._prefix = log, prefix

    def __getattr__(self, name):
        async def call(**kwargs):
            self._log.append((f"{self._prefix}.{name}", kwargs))
        return call


class FakeConnection:
    def __init__(self, events=()):
        self.log = []
        self.session = _Recorder(self.log, "session")
        self.input_audio_buffer = _Recorder(self.log, "input_audio_buffer")
        self.response = _Recorder(self.log, "response")
        self.conversation = NS(item=_Recorder(self.log, "conversation.item"))
        self._events = list(events)

    def __aiter__(self):
        async def gen():
            for e in self._events:
                yield e
        return gen()


class FakeCM:
    def __init__(self, conn=None, fail=False):
        self.conn, self.fail, self.exited = conn, fail, False

    async def __aenter__(self):
        if self.fail:
            raise OSError(f"connect failed for key {KEY}")
        return self.conn

    async def __aexit__(self, *a):
        self.exited = True


def _provider(conn=None, fail=False):
    cm = FakeCM(conn or FakeConnection(), fail)
    p = OpenAIRealtimeProvider(RealtimeConfig.for_provider("openai"), KEY,
                               tools=default_tools(), connection_factory=lambda: cm)
    return p, cm


@pytest.mark.asyncio
async def test_connect_configures_manual_turns_and_24k_audio():
    conn = FakeConnection()
    p, _ = _provider(conn)
    await p.connect()
    name, kwargs = conn.log[0]
    assert name == "session.update"
    s = kwargs["session"]
    assert s["type"] == "realtime" and s["model"] == "gpt-realtime-2.1"
    assert s["audio"]["input"]["turn_detection"] is None
    assert s["audio"]["input"]["format"] == {"type": "audio/pcm", "rate": 24000}
    assert [t["name"] for t in s["tools"]] == ["get_current_time", "get_system_status"]


@pytest.mark.asyncio
async def test_send_audio_resamples_16k_to_24k():
    conn = FakeConnection()
    p, _ = _provider(conn)
    await p.connect()
    await p.send_audio(b"\x00\x00" * 1600)  # 0.1 s at 16 kHz
    name, kwargs = conn.log[-1]
    assert name == "input_audio_buffer.append"
    assert len(base64.b64decode(kwargs["audio"])) == 2 * 2400  # 0.1 s at 24 kHz


@pytest.mark.asyncio
async def test_end_turn_commits_then_requests_response():
    conn = FakeConnection()
    p, _ = _provider(conn)
    await p.connect()
    await p.end_turn()
    assert [n for n, _ in conn.log[-2:]] == ["input_audio_buffer.commit", "response.create"]


@pytest.mark.asyncio
async def test_interrupt_cancels_response():
    conn = FakeConnection()
    p, _ = _provider(conn)
    await p.connect()
    await p.interrupt()
    assert conn.log[-1][0] == "response.cancel"


@pytest.mark.asyncio
async def test_tool_result_creates_item_then_response():
    conn = FakeConnection()
    p, _ = _provider(conn)
    await p.connect()
    await p.send_tool_result("c1", "get_current_time", {"time": "3:00 PM"})
    (n1, k1), (n2, _) = conn.log[-2:]
    assert n1 == "conversation.item.create" and n2 == "response.create"
    assert k1["item"] == {"type": "function_call_output", "call_id": "c1",
                          "output": json.dumps({"time": "3:00 PM"})}


@pytest.mark.asyncio
async def test_events_are_mapped():
    audio = base64.b64encode(b"\x01\x00\x02\x00").decode()
    conn = FakeConnection([
        NS(type="response.output_audio.delta", delta=audio),
        NS(type="response.output_audio_transcript.delta", delta="Hello"),
        NS(type="response.function_call_arguments.done", call_id="c9",
           name="get_current_time", arguments='{"x": 1}'),
        NS(type="session.updated"),
        NS(type="error", error=NS(message="bad thing")),
        NS(type="response.done"),
    ])
    p, _ = _provider(conn)
    await p.connect()
    got = [e async for e in p.events()]
    assert [e.kind for e in got] == [EventKind.AUDIO, EventKind.TRANSCRIPT,
                                     EventKind.TOOL_CALL, EventKind.ERROR, EventKind.TURN_DONE]
    assert got[0].audio == b"\x01\x00\x02\x00"
    assert got[2].call_id == "c9" and got[2].arguments == {"x": 1}
    assert got[3].text == "bad thing"


@pytest.mark.asyncio
async def test_connect_failure_is_provider_error_without_key():
    p, _ = _provider(fail=True)
    with pytest.raises(ProviderError) as exc:
        await p.connect()
    assert KEY not in str(exc.value)


@pytest.mark.asyncio
async def test_close_exits_connection():
    p, cm = _provider()
    await p.connect()
    await p.close()
    assert cm.exited


class _Closed(Exception):
    """Stands in for websockets' ConnectionClosedError."""


@pytest.mark.asyncio
async def test_connect_failure_names_the_provider_error_code_without_key():
    class FailingCM(FakeCM):
        async def __aenter__(self):
            raise _Closed(f"received 3000 (registered) invalid_request_error.invalid_api_key {KEY}")

    p = OpenAIRealtimeProvider(RealtimeConfig.for_provider("openai"), KEY,
                               connection_factory=lambda: FailingCM())
    with pytest.raises(ProviderError) as exc:
        await p.connect()
    assert "invalid_api_key" in str(exc.value)
    assert KEY not in str(exc.value)


@pytest.mark.asyncio
async def test_event_stream_failure_becomes_provider_error():
    class Broken(FakeConnection):
        def __aiter__(self):
            async def gen():
                raise RuntimeError(f"socket died {KEY}")
                yield  # pragma: no cover
            return gen()

    p, _ = _provider(Broken())
    await p.connect()
    with pytest.raises(ProviderError) as exc:
        async for _ in p.events():
            pass
    assert KEY not in str(exc.value)
