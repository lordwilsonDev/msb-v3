from __future__ import annotations

import json

import pytest

from msb_v3.speech.gate import NotEnrolledError
from msb_v3.speech.models import SpeakerIdentity
from msb_v3.speech.realtime.base import EventKind, ProviderError, RealtimeEvent
from msb_v3.speech.realtime.talk import PushToTalkController
from msb_v3.speech.realtime.tools import ToolRegistry, ToolSpec

CHUNK = b"\x10\x00" * 1600  # 0.1 s at 16 kHz


class FakeVerifier:
    def __init__(self, enrolled=True, speakers=("wilson",), raises=False, conf=0.9):
        self.enrolled, self.speakers, self.raises, self.conf = enrolled, list(speakers), raises, conf
        self.calls = 0

    def list_speakers(self):
        return self.speakers

    def verify(self, audio):
        self.calls += 1
        if self.raises:
            raise RuntimeError("model broke")
        return SpeakerIdentity(speaker_id="wilson" if self.enrolled else "unknown",
                               confidence=self.conf, is_enrolled=self.enrolled)


class FakeProvider:
    name = "fake"

    def __init__(self, script=None, fail_connect=False, fail_send=False):
        self.log, self.sent = [], []
        self.scripts = list(script or [[RealtimeEvent(EventKind.TURN_DONE)]])
        self.fail_connect, self.fail_send = fail_connect, fail_send

    async def connect(self):
        self.log.append("connect")
        if self.fail_connect:
            raise ProviderError("connect failed (OSError)")

    async def send_audio(self, data):
        if self.fail_send:
            raise ProviderError("send failed")
        self.sent.append(data)

    async def end_turn(self):
        self.log.append("end_turn")

    async def interrupt(self):
        self.log.append("interrupt")

    async def send_tool_result(self, call_id, name, output):
        self.log.append(("tool_result", call_id, name, output))

    async def events(self):
        batch = self.scripts.pop(0) if self.scripts else []
        for e in batch:
            yield e

    async def close(self):
        self.log.append("close")


class FakeSink:
    def __init__(self):
        self.played, self.stopped = [], 0

    def play(self, data):
        self.played.append(data)

    def stop(self):
        self.stopped += 1


async def frames(n):
    for _ in range(n):
        yield CHUNK


def _ctl(verifier=None, provider=None, tools=None, **kw):
    providers = []

    def factory():
        p = provider or FakeProvider()
        providers.append(p)
        return p

    spoken = []
    ctl = PushToTalkController(factory, verifier or FakeVerifier(), FakeSink(),
                               tools=tools, speak_local=spoken.append, **kw)
    return ctl, providers, spoken


@pytest.mark.asyncio
async def test_unverified_speaker_sends_nothing_and_creates_no_session():
    ctl, providers, spoken = _ctl(FakeVerifier(enrolled=False, conf=0.4))
    r = await ctl.run_turn(frames(30))
    assert r.sent is False and r.reason == "unverified speaker" and r.confidence == 0.4
    assert providers == [] and spoken == []


@pytest.mark.asyncio
async def test_verifier_error_sends_nothing():
    ctl, providers, _ = _ctl(FakeVerifier(raises=True))
    r = await ctl.run_turn(frames(30))
    assert r.sent is False and r.reason == "verification error" and providers == []


@pytest.mark.asyncio
async def test_nobody_enrolled_refuses_before_reading_audio():
    read = []

    async def tracked():
        read.append(1)
        yield CHUNK

    ctl, providers, _ = _ctl(FakeVerifier(speakers=()))
    with pytest.raises(NotEnrolledError):
        await ctl.run_turn(tracked())
    assert read == [] and providers == []


@pytest.mark.asyncio
async def test_verify_uses_only_first_verify_seconds():
    v = FakeVerifier()
    ctl, _, _ = _ctl(v, verify_seconds=0.5, min_verify_seconds=0.5)
    await ctl.run_turn(frames(30))
    assert v.calls == 1


@pytest.mark.asyncio
async def test_verified_sends_buffer_then_live_frames_in_order():
    p = FakeProvider()
    ctl, _, _ = _ctl(provider=p, verify_seconds=0.5, min_verify_seconds=0.5)
    r = await ctl.run_turn(frames(12))
    assert r.sent is True and r.seconds_sent == pytest.approx(1.2)
    assert b"".join(p.sent) == CHUNK * 12
    assert p.log[:1] == ["connect"] and "end_turn" in p.log


@pytest.mark.asyncio
async def test_short_turn_under_verify_window_still_verified_then_sent():
    p = FakeProvider()
    ctl, _, _ = _ctl(provider=p, verify_seconds=1.5)
    r = await ctl.run_turn(frames(9))  # 0.9 s: above the 0.8 s minimum, below the window
    assert r.sent is True and b"".join(p.sent) == CHUNK * 9


@pytest.mark.asyncio
async def test_reply_audio_played_and_transcript_kept():
    p = FakeProvider([[RealtimeEvent(EventKind.AUDIO, audio=b"ab"),
                       RealtimeEvent(EventKind.TRANSCRIPT, text="It is "),
                       RealtimeEvent(EventKind.TRANSCRIPT, text="three."),
                       RealtimeEvent(EventKind.TURN_DONE)]])
    ctl, _, _ = _ctl(provider=p)
    r = await ctl.run_turn(frames(20))
    assert ctl.sink.played == [b"ab"] and r.transcript == "It is three."


@pytest.mark.asyncio
async def test_tool_call_dispatched_and_reading_continues_to_final_turn_done():
    reg = ToolRegistry([ToolSpec("get_current_time", "t", {"type": "object", "properties": {}},
                                 lambda a: {"time": "3 PM"})])
    p = FakeProvider([[RealtimeEvent(EventKind.TOOL_CALL, call_id="c1", name="get_current_time"),
                       RealtimeEvent(EventKind.TURN_DONE),
                       RealtimeEvent(EventKind.AUDIO, audio=b"3pm"),
                       RealtimeEvent(EventKind.TURN_DONE)]])
    ctl, _, _ = _ctl(provider=p, tools=reg)
    await ctl.run_turn(frames(20))
    assert ("tool_result", "c1", "get_current_time", {"time": "3 PM"}) in p.log
    assert ctl.sink.played == [b"3pm"]


@pytest.mark.asyncio
async def test_tool_call_without_registry_returns_error_result():
    p = FakeProvider([[RealtimeEvent(EventKind.TOOL_CALL, call_id="c1", name="x"),
                       RealtimeEvent(EventKind.TURN_DONE), RealtimeEvent(EventKind.TURN_DONE)]])
    ctl, _, _ = _ctl(provider=p)
    await ctl.run_turn(frames(20))
    assert ("tool_result", "c1", "x", {"error": "no tools available"}) in p.log


@pytest.mark.asyncio
async def test_connect_failure_speaks_offline_and_sends_nothing():
    p = FakeProvider(fail_connect=True)
    ctl, _, spoken = _ctl(provider=p)
    r = await ctl.run_turn(frames(20))
    assert r.sent is False and r.reason.startswith("offline") and spoken == ["I'm offline."]
    assert "close" in p.log


@pytest.mark.asyncio
async def test_send_failure_mid_turn_speaks_offline():
    p = FakeProvider(fail_send=True)
    ctl, _, spoken = _ctl(provider=p)
    r = await ctl.run_turn(frames(20))
    assert r.reason.startswith("offline") and spoken == ["I'm offline."]


@pytest.mark.asyncio
async def test_session_reused_across_turns():
    p = FakeProvider([[RealtimeEvent(EventKind.TURN_DONE)], [RealtimeEvent(EventKind.TURN_DONE)]])
    ctl, providers, _ = _ctl(provider=p)
    await ctl.run_turn(frames(20))
    await ctl.run_turn(frames(20))
    assert p.log.count("connect") == 1


@pytest.mark.asyncio
async def test_session_cap_reconnects_with_notice():
    now = [0.0]
    p = FakeProvider([[RealtimeEvent(EventKind.TURN_DONE)], [RealtimeEvent(EventKind.TURN_DONE)]])
    ctl, _, spoken = _ctl(provider=p, session_cap_seconds=10, clock=lambda: now[0])
    await ctl.run_turn(frames(20))
    now[0] = 11.0
    await ctl.run_turn(frames(20))
    assert p.log.count("connect") == 2 and "close" in p.log
    assert spoken == ["Session limit reached, starting fresh."]


@pytest.mark.asyncio
async def test_interrupt_stops_playback_and_interrupts_provider():
    ctl_holder = {}

    class Interrupting(FakeProvider):
        async def events(self):
            yield RealtimeEvent(EventKind.AUDIO, audio=b"a")
            ctl_holder["ctl"].interrupt()
            yield RealtimeEvent(EventKind.AUDIO, audio=b"b")
            yield RealtimeEvent(EventKind.TURN_DONE)

    p = Interrupting()
    ctl, _, _ = _ctl(provider=p)
    ctl_holder["ctl"] = ctl
    await ctl.run_turn(frames(20))
    assert "interrupt" in p.log and ctl.sink.stopped >= 1
    assert ctl.sink.played == [b"a"]


@pytest.mark.asyncio
async def test_usage_logged(tmp_path):
    log = tmp_path / "usage.jsonl"
    ctl, _, _ = _ctl(usage_log=log)
    await ctl.run_turn(frames(20))
    row = json.loads(log.read_text().strip())
    assert row["provider"] == "fake" and row["seconds_sent"] == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_no_usage_logged_when_nothing_sent(tmp_path):
    log = tmp_path / "usage.jsonl"
    ctl, _, _ = _ctl(FakeVerifier(enrolled=False), usage_log=log)
    await ctl.run_turn(frames(20))
    assert not log.exists()


@pytest.mark.asyncio
async def test_too_short_turn_is_not_verified_and_says_so():
    v = FakeVerifier()
    ctl, providers, _ = _ctl(v, min_verify_seconds=0.8)
    r = await ctl.run_turn(frames(5))  # 0.5 s
    assert r.sent is False and r.reason == "too short to verify"
    assert v.calls == 0 and providers == []


@pytest.mark.asyncio
async def test_no_reply_times_out_and_resets_session():
    import asyncio as aio

    class Silent(FakeProvider):
        async def events(self):
            await aio.sleep(10)
            yield RealtimeEvent(EventKind.TURN_DONE)

    p = Silent()
    ctl, _, spoken = _ctl(provider=p, reply_timeout_seconds=0.05)
    r = await ctl.run_turn(frames(20))
    assert r.sent is True and r.reason == "no reply (timed out)"
    assert "close" in p.log and spoken == ["No reply. Try again."]
