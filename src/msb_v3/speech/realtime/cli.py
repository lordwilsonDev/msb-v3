"""Push-to-talk voice assistant.

    voice              talk (Gemini); sets up your voiceprint first if needed
    voice enroll       redo your voiceprint
    voice --provider openai | --debug | --button media

(`voice` is ~/bin/voice, a wrapper for ``python -m msb_v3.speech.realtime.cli``.)
Press Enter, wait for the tink, talk. The turn ends by itself after
``--silence`` seconds of quiet; pressing again ends it early. Pressing while
it is replying interrupts it and starts a new turn. Ctrl+C quits.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, AsyncIterator, Callable, List, Optional

from msb_v3.speech.realtime.base import RealtimeConfig, RealtimeProvider
from msb_v3.speech.realtime.keys import (
    MissingKeyError,
    load_gemini_key,
    load_openai_key,
)
from msb_v3.speech.realtime.tools import ToolRegistry, default_tools
from msb_v3.speech.realtime.turn import EndOfTurnDetector, webrtc_is_speech

if TYPE_CHECKING:
    from msb_v3.speech.realtime.talk import TurnResult

DEFAULT_USAGE_LOG = Path("~/.msb/realtime-usage.jsonl").expanduser()
DEFAULT_ENROLLMENTS = Path("~/.msb/voice-enrollment.json").expanduser()

#: Short macOS system sounds so you know where you are without looking.
CUE_LISTEN = "Tink"
CUE_DONE = "Pop"
CUE_REFUSED = "Basso"
CUE_SECONDS = 0.35  # Tink is ~0.3 s


def _cue(name: str) -> None:
    """Play a system sound without blocking; silence if unavailable."""
    try:
        subprocess.Popen(["afplay", f"/System/Library/Sounds/{name}.aiff"])  # noqa: S603,S607
    except OSError:
        pass


DEBUG_DIR = Path("~/.msb/voice-debug").expanduser()


def _save_debug_turn(chunks: List[bytes], timeline: List[str]) -> None:
    """Save the turn's audio locally and print a 0.1 s speech map (S = speech)."""
    import wave
    from datetime import datetime

    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    path = DEBUG_DIR / f"turn-{datetime.now():%H%M%S}.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"".join(chunks))
    print(f"  debug: {len(chunks) / 10:.1f}s captured  map={''.join(timeline)}  -> {path}")


_REASONS = {
    "unverified speaker": "didn't recognize your voice",
    "too short to verify": "too short — say a bit more",
    "verification error": "voice check failed",
    "no reply (timed out)": "no reply from the assistant",
}


def _describe(result: "TurnResult", debug: bool) -> str:
    """One readable line per turn; raw numbers only with --debug."""
    if result.sent and not result.reason:
        line = f"  you: ok (voice {result.confidence:.2f})  assistant: {result.transcript or '…'}"
    else:
        why = _REASONS.get(result.reason, result.reason)
        line = f"  not sent: {why}" if not result.sent else f"  problem: {why}"
    if debug:
        line += (f"\n  [sent={result.sent} reason={result.reason!r} "
                 f"voice={result.confidence:.2f} seconds={result.seconds_sent:.1f}]")
    return line


def _provider_factory(
    name: str, model: Optional[str], tools: ToolRegistry
) -> Callable[[], RealtimeProvider]:
    config = RealtimeConfig.for_provider(name, model)
    if name == "openai":
        from msb_v3.speech.realtime.openai_rt import OpenAIRealtimeProvider

        openai_key = load_openai_key()
        return lambda: OpenAIRealtimeProvider(config, openai_key, tools=tools)
    from msb_v3.speech.realtime.gemini_live import GeminiLiveProvider

    gemini_key = load_gemini_key()
    return lambda: GeminiLiveProvider(config, gemini_key, tools=tools)


async def _loop(args: argparse.Namespace) -> None:
    from msb_v3.speech.realtime.audio_io import MicStream, Speaker
    from msb_v3.speech.realtime.button import EnterKeyButton, MediaKeyButton
    from msb_v3.speech.realtime.talk import PushToTalkController
    from msb_v3.speech.speaker import SpeakerVerifier

    tools = default_tools()
    factory = _provider_factory(args.provider, args.model, tools)
    verifier = SpeakerVerifier(enrollments_path=args.enrollments)
    speaker = Speaker()
    # Verify on the whole turn (up to --verify-seconds), not just its opening:
    # a short opening window measured mostly silence and scored the owner low.
    ctl = PushToTalkController(factory, verifier, speaker, tools=tools,
                               verify_seconds=args.verify_seconds,
                               usage_log=DEFAULT_USAGE_LOG)
    button = MediaKeyButton() if args.button == "media" else EnterKeyButton()
    mic = MicStream()
    is_speech = webrtc_is_speech()
    print(f"Ready ({args.provider}). Press Enter, wait for the tink, talk. "
          "It stops when you pause. Enter while it talks cuts it off. Ctrl+C quits.")
    start_now = False
    try:
        while True:
            if not start_now:
                await button.wait_press()
            start_now = False
            stop = asyncio.Event()
            detector = EndOfTurnDetector(is_speech, silence_after=args.silence)
            # Cue first, then open the mic, so the cue isn't heard as speech.
            _cue(CUE_LISTEN)
            await asyncio.sleep(CUE_SECONDS)
            mic.start()
            print("  listening…", end="", flush=True)

            captured: List[bytes] = []
            timeline: List[str] = []

            async def auto_end(frames: AsyncIterator[bytes],
                               det: EndOfTurnDetector = detector,
                               ev: asyncio.Event = stop,
                               cap: List[bytes] = captured,
                               tl: List[str] = timeline) -> AsyncIterator[bytes]:
                async for chunk in frames:
                    if args.debug:
                        cap.append(chunk)
                        tl.append("S" if is_speech(chunk) else ".")
                    yield chunk
                    if det.update(chunk):
                        ev.set()

            turn = asyncio.create_task(ctl.run_turn(auto_end(mic.frames_until(stop))))
            ender = asyncio.create_task(button.wait_press())
            stopped = asyncio.create_task(stop.wait())
            await asyncio.wait({ender, stopped}, return_when=asyncio.FIRST_COMPLETED)
            stop.set()
            for task in (ender, stopped):
                if not task.done():
                    task.cancel()
            _cue(CUE_DONE)
            print(" thinking…", flush=True)

            # A press while it replies interrupts, and that press starts the next turn.
            interrupter = asyncio.create_task(button.wait_press())
            done, _ = await asyncio.wait({turn, interrupter}, return_when=asyncio.FIRST_COMPLETED)
            if interrupter in done:
                ctl.interrupt()
                start_now = True
            result = await turn
            if not interrupter.done():
                interrupter.cancel()
            if not result.sent:
                _cue(CUE_REFUSED)
            if args.debug:
                _save_debug_turn(captured, timeline)
            print(_describe(result, args.debug))
    finally:
        await ctl.close()
        speaker.close()


ENROLL_PROMPTS = [
    "What time is it, and what's on my schedule today?",
    "Give me the system status and anything that looks wrong.",
    "Check the queue and tell me what needs my attention first.",
    "Remind me to call the supplier back tomorrow morning.",
    "Summarize the last message and read me the important part.",
]


def needs_enrollment(path: Path) -> bool:
    """True when there is no usable voiceprint at ``path``."""
    try:
        return not json.loads(path.read_text())
    except (OSError, ValueError):
        return True


def enroll(path: Path, speaker_id: str = "wilson") -> None:
    """Record 5 command-style samples (4 s each) and save a private voiceprint."""
    from msb_v3.speech.capture import capture_from_microphone
    from msb_v3.speech.speaker import SpeakerVerifier

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    verifier = SpeakerVerifier(enrollments_path=str(path))
    print(f"Voice setup: {len(ENROLL_PROMPTS)} short lines, 4 seconds each. "
          "Talk the way you'll talk to it.\n")
    for i, prompt in enumerate(ENROLL_PROMPTS, 1):
        input(f'[{i}/{len(ENROLL_PROMPTS)}] Press Enter, then say: "{prompt}" ')
        verifier.enroll(speaker_id, capture_from_microphone(duration_seconds=4.0))
    os.chmod(path, 0o600)  # a voiceprint is biometric data
    print(f"Saved your voiceprint to {path}\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="voice",
        description="Push-to-talk voice assistant. `voice` to talk, `voice enroll` to redo "
                    "your voiceprint.")
    parser.add_argument("command", nargs="?", choices=["talk", "enroll"], default="talk")
    parser.add_argument("--provider", choices=["openai", "gemini"], default="gemini")
    parser.add_argument("--model", default=None)
    parser.add_argument("--button", choices=["enter", "media"], default="enter")
    parser.add_argument("--silence", type=float, default=1.0,
                        help="seconds of quiet after speech that end the turn")
    parser.add_argument("--verify-seconds", type=float, default=8.0,
                        help="audio (from the start of the turn) used for the voice check")
    parser.add_argument("--debug", action="store_true",
                        help="save each turn to ~/.msb/voice-debug and print a speech map")
    parser.add_argument("--enrollments", default=str(DEFAULT_ENROLLMENTS),
                        help="voiceprint file (default: %(default)s)")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.debug else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    try:
        path = Path(args.enrollments).expanduser()
        if args.command == "enroll":
            enroll(path)
            return 0
        if needs_enrollment(path):
            print("No voiceprint yet — let's set one up first.\n")
            enroll(path)
        asyncio.run(_loop(args))
    except MissingKeyError as exc:
        print(f"error: {exc}")
        return 2
    except KeyboardInterrupt:
        print("\nbye")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
