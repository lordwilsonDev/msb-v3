from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from msb_v3.speech.realtime.base import EventKind, ProviderError, RealtimeConfig
from msb_v3.speech.realtime.gemini_live import GeminiLiveProvider
from msb_v3.speech.realtime.tools import default_tools

KEY = "AIza-test-SECRET-GEMINI"


def _content(**kw):
    base = dict(model_turn=None, output_transcription=None, turn_complete=None, interrupted=None)
    base.update(kw)
    return NS(server_content=NS(**base), tool_call=None)


class FakeSession:
    def __init__(self, batches=()):
        self.sent = []
        self._batches = [list(b) for b in batches]

    async def send_realtime_input(self, **kw):
        self.sent.append(("realtime", kw))

    async def send_tool_response(self, **kw):
        self.sent.append(("tool", kw))

    def receive(self):
        batch = self._batches.pop(0) if self._batches else []

        async def gen():
            for m in batch:
                yield m
        return gen()


class FakeCM:
    def __init__(self, session=None, fail=False):
        self.session, self.fail, self.exited = session, fail, False

    async def __aenter__(self):
        if self.fail:
            raise OSError(f"bad key {KEY}")
        return self.session

    async def __aexit__(self, *a):
        self.exited = True


def _provider(session=None, fail=False):
    cm = FakeCM(session or FakeSession(), fail)
    p = GeminiLiveProvider(RealtimeConfig.for_provider("gemini"), KEY,
                           tools=default_tools(), session_factory=lambda: cm)
    return p, cm


@pytest.mark.asyncio
async def test_first_audio_of_turn_sends_activity_start():
    s = FakeSession()
    p, _ = _provider(s)
    await p.connect()
    await p.send_audio(b"\x00\x00" * 160)
    await p.send_audio(b"\x00\x00" * 160)
    kinds = [list(kw.keys())[0] for _, kw in s.sent]
    assert kinds == ["activity_start", "audio", "audio"]
    blob = s.sent[1][1]["audio"]
    assert blob.mime_type == "audio/pcm;rate=16000"
    assert blob.data == b"\x00\x00" * 160


@pytest.mark.asyncio
async def test_end_turn_sends_activity_end_and_next_turn_restarts():
    s = FakeSession()
    p, _ = _provider(s)
    await p.connect()
    await p.send_audio(b"\x00\x00")
    await p.end_turn()
    await p.send_audio(b"\x00\x00")
    kinds = [list(kw.keys())[0] for _, kw in s.sent]
    assert kinds == ["activity_start", "audio", "activity_end", "activity_start", "audio"]


@pytest.mark.asyncio
async def test_tool_result_sent_as_function_response():
    s = FakeSession()
    p, _ = _provider(s)
    await p.connect()
    await p.send_tool_result("id7", "get_current_time", {"time": "3 PM"})
    kind, kw = s.sent[-1]
    fr = kw["function_responses"][0]
    assert kind == "tool" and fr.id == "id7" and fr.name == "get_current_time"
    assert fr.response == {"time": "3 PM"}


@pytest.mark.asyncio
async def test_events_mapped_across_receive_batches():
    part = NS(inline_data=NS(data=b"\x01\x00"))
    tool_msg = NS(server_content=None,
                  tool_call=NS(function_calls=[NS(id="f1", name="get_current_time", args={"a": 1})]))
    s = FakeSession([
        [tool_msg],
        [_content(model_turn=NS(parts=[part])),
         _content(output_transcription=NS(text="Hi")),
         _content(turn_complete=True)],
    ])
    p, _ = _provider(s)
    await p.connect()
    got = []
    async for e in p.events():
        got.append(e)
        if e.kind is EventKind.TURN_DONE:
            break
    assert [e.kind for e in got] == [EventKind.TOOL_CALL, EventKind.AUDIO,
                                     EventKind.TRANSCRIPT, EventKind.TURN_DONE]
    assert got[0].call_id == "f1" and got[0].arguments == {"a": 1}
    assert got[1].audio == b"\x01\x00"


@pytest.mark.asyncio
async def test_interrupt_drops_audio_until_turn_complete():
    part = NS(inline_data=NS(data=b"\x01\x00"))
    s = FakeSession([[_content(model_turn=NS(parts=[part])), _content(turn_complete=True)]])
    p, _ = _provider(s)
    await p.connect()
    await p.interrupt()
    got = []
    async for e in p.events():
        got.append(e.kind)
        if e.kind is EventKind.TURN_DONE:
            break
    assert got == [EventKind.TURN_DONE]


@pytest.mark.asyncio
async def test_connect_failure_is_provider_error_without_key():
    p, _ = _provider(fail=True)
    with pytest.raises(ProviderError) as exc:
        await p.connect()
    assert KEY not in str(exc.value)


@pytest.mark.asyncio
async def test_receive_failure_becomes_provider_error_without_key():
    class Broken(FakeSession):
        def receive(self):
            async def gen():
                raise RuntimeError(f"1000 None {KEY}")
                yield  # pragma: no cover
            return gen()

    p, _ = _provider(Broken())
    await p.connect()
    with pytest.raises(ProviderError) as exc:
        async for _ in p.events():
            pass
    assert KEY not in str(exc.value)
