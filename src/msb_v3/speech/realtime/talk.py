"""Push-to-talk controller: verify locally, then (and only then) talk to the cloud.

Invariant: no audio reaches a provider until the speaker is verified. The
provider is not even created for an unverified turn.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import AsyncIterator, Callable, List, Optional, Protocol

from msb_v3.speech.gate import NotEnrolledError, SpeakerVerifying
from msb_v3.speech.models import AudioBuffer
from msb_v3.speech.realtime.audio import MIC_RATE, pcm16_seconds, pcm16_to_float
from msb_v3.speech.realtime.base import EventKind, ProviderError, RealtimeProvider
from msb_v3.speech.realtime.tools import ToolRegistry

_log = logging.getLogger(__name__)

OFFLINE_NOTICE = "I'm offline."
CAP_NOTICE = "Session limit reached, starting fresh."
NO_REPLY_NOTICE = "No reply. Try again."


def say(text: str) -> None:
    """Speak locally with macOS ``say`` without blocking the event loop."""
    try:
        subprocess.Popen(["say", text])  # noqa: S603,S607 — fixed local binary
    except OSError as exc:
        _log.info("local speech unavailable (%s): %s", type(exc).__name__, text)


class AudioSink(Protocol):
    def play(self, pcm16_24k: bytes) -> None: ...
    def stop(self) -> None: ...


@dataclass
class TurnResult:
    sent: bool
    reason: str = ""
    speaker_id: str = "unknown"
    confidence: float = 0.0
    seconds_sent: float = 0.0
    transcript: str = ""


class PushToTalkController:
    def __init__(
        self,
        provider_factory: Callable[[], RealtimeProvider],
        verifier: SpeakerVerifying,
        sink: AudioSink,
        *,
        tools: Optional[ToolRegistry] = None,
        speak_local: Callable[[str], None] = say,
        verify_seconds: float = 1.5,
        min_verify_seconds: float = 0.8,
        session_cap_seconds: float = 300.0,
        reply_timeout_seconds: float = 20.0,
        usage_log: Optional[Path] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.provider_factory = provider_factory
        self.verifier = verifier
        self.sink = sink
        self.tools = tools
        self.speak_local = speak_local
        self.verify_seconds = verify_seconds
        self.min_verify_seconds = min_verify_seconds
        self.session_cap_seconds = session_cap_seconds
        self.reply_timeout_seconds = reply_timeout_seconds
        self.usage_log = usage_log
        self.clock = clock
        self._provider: Optional[RealtimeProvider] = None
        self._provider_name = "unknown"
        self._opened_at = 0.0
        self._interrupt_requested = False

    # ── public ─────────────────────────────────────────────────────────

    def interrupt(self) -> None:
        """Stop playback now; the running reply loop tells the provider."""
        self._interrupt_requested = True
        self.sink.stop()

    async def close(self) -> None:
        if self._provider is not None:
            provider, self._provider = self._provider, None
            try:
                await provider.close()
            except (ProviderError, OSError) as exc:
                _log.info("provider close failed (%s)", type(exc).__name__)

    async def run_turn(self, frames: AsyncIterator[bytes]) -> TurnResult:
        """Run one push-to-talk turn. ``frames`` ends when the button ends the turn."""
        if not self.verifier.list_speakers():
            raise NotEnrolledError(
                "push-to-talk requires an enrolled speaker; enroll with SpeakerVerifier.enroll()"
            )
        self._interrupt_requested = False

        # 1. Buffer the verification window locally.
        buffered: List[bytes] = []
        exhausted = True
        async for chunk in frames:
            buffered.append(chunk)
            if pcm16_seconds(b"".join(buffered), MIC_RATE) >= self.verify_seconds:
                exhausted = False
                break
        head = b"".join(buffered)

        # Too little audio to judge a voice: say so rather than call it a stranger.
        if pcm16_seconds(head, MIC_RATE) < self.min_verify_seconds:
            await self._drain(frames, exhausted)
            return TurnResult(sent=False, reason="too short to verify")

        # 2. Verify. Fail closed: nothing has been sent and no provider exists.
        try:
            identity = self.verifier.verify(
                AudioBuffer(samples=pcm16_to_float(head), sample_rate=MIC_RATE))
        except Exception as exc:  # noqa: BLE001 — any verifier failure is a refusal
            _log.info("push-to-talk verification error (%s)", type(exc).__name__)
            await self._drain(frames, exhausted)
            return TurnResult(sent=False, reason="verification error")
        if not identity.is_enrolled:
            _log.info("push-to-talk ignored unverified speaker (confidence %.2f)",
                      identity.confidence)
            await self._drain(frames, exhausted)
            return TurnResult(sent=False, reason="unverified speaker",
                              speaker_id=identity.speaker_id, confidence=identity.confidence)

        # 3. Verified: talk to the provider.
        result = TurnResult(sent=False, speaker_id=identity.speaker_id,
                            confidence=identity.confidence)
        seconds = 0.0
        try:
            provider = await self._ensure_session()
            await provider.send_audio(head)
            seconds += pcm16_seconds(head, MIC_RATE)
            result.sent = True
            if not exhausted:
                async for chunk in frames:
                    await provider.send_audio(chunk)
                    seconds += pcm16_seconds(chunk, MIC_RATE)
            await provider.end_turn()
            await self._consume_reply(provider, result)
        except asyncio.TimeoutError:
            # The provider went quiet: don't hang the loop, and don't reuse a
            # session whose state we no longer know.
            result.reason = "no reply (timed out)"
            self.speak_local(NO_REPLY_NOTICE)
            await self.close()
        except (ProviderError, OSError) as exc:
            detail = str(exc) if isinstance(exc, ProviderError) else type(exc).__name__
            result.reason = f"offline: {detail}"
            self.speak_local(OFFLINE_NOTICE)
            await self.close()
        finally:
            result.seconds_sent = seconds
            self._record_usage(seconds)
        return result

    # ── internals ──────────────────────────────────────────────────────

    async def _drain(self, frames: AsyncIterator[bytes], exhausted: bool) -> None:
        """Consume (and discard) the rest of a refused turn."""
        if exhausted:
            return
        async for _ in frames:
            pass

    async def _ensure_session(self) -> RealtimeProvider:
        if self._provider is not None and self.clock() - self._opened_at > self.session_cap_seconds:
            await self.close()
            self.speak_local(CAP_NOTICE)
        if self._provider is None:
            provider = self.provider_factory()
            self._provider = provider
            self._provider_name = getattr(provider, "name", "unknown")
            await provider.connect()
            self._opened_at = self.clock()
        return self._provider

    async def _consume_reply(self, provider: RealtimeProvider, result: TurnResult) -> None:
        tool_pending = False
        text: List[str] = []
        stream = provider.events().__aiter__()
        while True:
            try:
                ev = await asyncio.wait_for(stream.__anext__(), self.reply_timeout_seconds)
            except StopAsyncIteration:
                break
            if self._interrupt_requested:
                await provider.interrupt()
                self.sink.stop()
                break
            if ev.kind is EventKind.AUDIO:
                self.sink.play(ev.audio)
            elif ev.kind is EventKind.TRANSCRIPT:
                text.append(ev.text)
            elif ev.kind is EventKind.TOOL_CALL:
                output = (self.tools.dispatch(ev.name, ev.arguments) if self.tools
                          else {"error": "no tools available"})
                await provider.send_tool_result(ev.call_id, ev.name, output)
                tool_pending = True
            elif ev.kind is EventKind.ERROR:
                result.reason = f"provider error: {ev.text}"
                break
            elif ev.kind is EventKind.TURN_DONE:
                if tool_pending:
                    tool_pending = False
                    continue
                break
        result.transcript = "".join(text)

    def _record_usage(self, seconds: float) -> None:
        if not self.usage_log or seconds <= 0:
            return
        row = {"date": date.today().isoformat(), "provider": self._provider_name,
               "seconds_sent": round(seconds, 3)}
        self.usage_log.parent.mkdir(parents=True, exist_ok=True)
        with self.usage_log.open("a") as fh:
            fh.write(json.dumps(row) + "\n")
