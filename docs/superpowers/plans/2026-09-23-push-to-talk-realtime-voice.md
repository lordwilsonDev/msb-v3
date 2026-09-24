# Push-to-talk Realtime Voice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Press a talk button, have the Mac verify the owner's voice locally, and only then hold a spoken conversation through OpenAI Realtime or Gemini Live.

**Architecture:** A provider-neutral `RealtimeProvider` Protocol with two adapters (OpenAI, Gemini). A `PushToTalkController` owns the verify-then-send decision, playback, tool dispatch, session cap and usage log, and depends only on injected interfaces so it is fully testable offline. Real audio I/O, the talk button and a CLI sit at the edge.

**Tech Stack:** Python 3.12, asyncio, `openai` 2.53.0 (`AsyncOpenAI().realtime.connect`), `google-genai` 2.12.1 (`client.aio.live.connect`), numpy, pyaudio, pyobjc (AppKit) for the media-key spike, pytest + pytest-asyncio (strict mode: mark async tests with `@pytest.mark.asyncio`).

**Spec:** `docs/superpowers/specs/2026-09-23-push-to-talk-realtime-voice-design.md`

## Global Constraints

- **No audio leaves the Mac before speaker verification passes.** Unverified, verifier error, or nobody enrolled → zero `send_audio` calls.
- Nobody enrolled → refuse to start with `NotEnrolledError` (from `msb_v3.speech.gate`).
- Mic capture is 16 kHz mono PCM16 little-endian. OpenAI input is PCM16 **24 kHz** (`{"type": "audio/pcm", "rate": 24000}`); Gemini input is `audio/pcm;rate=16000`. Both outputs are PCM16 24 kHz.
- Turn boundaries come from the button: OpenAI `turn_detection: None`; Gemini `automatic_activity_detection.disabled=True` with explicit `activity_start` / `activity_end`.
- Model ids live in config. Defaults: OpenAI `gpt-realtime-2.1`, Gemini `gemini-3.1-flash-live-preview`.
- Keys: `OPENAI_API_KEY` from env or `msb-v3/.env`. Gemini: `GEMINI_API_KEY` env, else file at `GEMINI_API_KEY_FILE`, default `~/.secrets/gemini-api-key.txt`; refuse a key file readable by group/others. Key text never appears in logs or exception messages.
- Session cap default 300 s. Usage log: JSONL line per turn `{date, provider, seconds_sent}`.
- Network/provider failure → speak "I'm offline" locally (macOS `say`), no retry loop.
- Tools: only `RiskLevel.LOW` read-only tools may run; anything else returns an error payload.
- This path must not import or load Whisper.
- **Do not commit.** Wilson commits. Stage nothing.
- Speech subsystem is EXPERIMENTAL; keep new deps optional (lazy imports of `openai`, `google.genai`, `pyaudio`, `AppKit`).

## File Structure

```
src/msb_v3/speech/realtime/
  __init__.py      package marker + public names
  keys.py          API key loading (env / .env / key file), permission check
  audio.py         PCM16 helpers: bytes<->float, resample, duration
  base.py          RealtimeEvent, RealtimeConfig, RealtimeProvider Protocol
  tools.py         ToolSpec, ToolRegistry, default_tools()
  openai_rt.py     OpenAI Realtime adapter
  gemini_live.py   Gemini Live adapter
  talk.py          PushToTalkController, TurnResult
  audio_io.py      PyAudio mic stream + speaker (edge, live-tested only)
  button.py        EnterKeyButton, parse_media_key(), MediaKeyButton
  cli.py           `python -m msb_v3.speech.realtime.cli`
tests/speech/realtime/
  __init__.py
  test_keys.py  test_audio.py  test_tools.py
  test_openai_rt.py  test_gemini_live.py  test_talk.py  test_button.py
```

---

### Task 1: Headset button spike (manual, scratchpad — no repo change)

**Files:** Create in the session scratchpad: `media_key_spike.py`

- [ ] **Step 1: Write the spike**

```python
"""Print what the headset button sends on macOS. Ctrl+C to stop.

Needs Accessibility / Input Monitoring for the terminal app (System Settings
→ Privacy & Security). Press the headset button a few times while running.
"""
from AppKit import NSApplication, NSEvent, NSSystemDefinedMask, NSKeyDownMask
from PyObjCTools import AppHelper

def handler(event):
    if event.type() == 14:  # NSEventTypeSystemDefined
        data1 = event.data1()
        key_code = (data1 & 0xFFFF0000) >> 16
        key_down = ((data1 & 0xFF00) >> 8) == 0xA
        print(f"system-defined subtype={event.subtype()} key_code={key_code} down={key_down}")
    else:
        print(f"keydown keyCode={event.keyCode()} chars={event.characters()!r}")

NSApplication.sharedApplication()
NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(NSSystemDefinedMask | NSKeyDownMask, handler)
print("Listening… press the headset button.")
AppHelper.runConsoleEventLoop()
```

- [ ] **Step 2: Run with Wilson pressing the button**

Run: `python3 <scratchpad>/media_key_spike.py`
Expected: lines like `system-defined subtype=8 key_code=16 down=True` (16 = play/pause). Record the result.
Decision: if play/pause arrives → Task 9 wires `MediaKeyButton`. If nothing arrives → CLI default stays `--button enter` and the result is recorded in the spec's open items.

---

### Task 2: API key loading

**Files:**
- Create: `src/msb_v3/speech/realtime/__init__.py`, `src/msb_v3/speech/realtime/keys.py`
- Create: `tests/speech/realtime/__init__.py` (empty), `tests/speech/realtime/test_keys.py`

**Interfaces:**
- Produces: `MissingKeyError(RuntimeError)`, `InsecureKeyFileError(MissingKeyError)`, `load_openai_key(env: Mapping[str,str] | None = None, dotenv_path: Path | None = None) -> str`, `load_gemini_key(env: Mapping[str,str] | None = None, key_file: Path | None = None) -> str`

- [ ] **Step 1: Write the failing tests**

```python
"""Key loading: env first, then file; never leak key text."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from msb_v3.speech.realtime.keys import (
    InsecureKeyFileError,
    MissingKeyError,
    load_gemini_key,
    load_openai_key,
)

SECRET = "sk-test-SECRET-VALUE-123"


def _key_file(tmp_path: Path, text: str, mode: int = 0o600) -> Path:
    p = tmp_path / "gemini-api-key.txt"
    p.write_text(text)
    os.chmod(p, mode)
    return p


class TestOpenAIKey:
    def test_env_wins(self, tmp_path):
        assert load_openai_key(env={"OPENAI_API_KEY": SECRET}, dotenv_path=tmp_path / "none") == SECRET

    def test_reads_dotenv(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text(f"OTHER=1\nOPENAI_API_KEY={SECRET}\n")
        assert load_openai_key(env={}, dotenv_path=env_file) == SECRET

    def test_dotenv_strips_quotes(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text(f'OPENAI_API_KEY="{SECRET}"\n')
        assert load_openai_key(env={}, dotenv_path=env_file) == SECRET

    def test_missing_raises(self, tmp_path):
        with pytest.raises(MissingKeyError):
            load_openai_key(env={}, dotenv_path=tmp_path / "none")

    def test_empty_value_is_missing(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("OPENAI_API_KEY=\n")
        with pytest.raises(MissingKeyError):
            load_openai_key(env={}, dotenv_path=env_file)


class TestGeminiKey:
    def test_env_wins(self, tmp_path):
        assert load_gemini_key(env={"GEMINI_API_KEY": SECRET}, key_file=tmp_path / "x") == SECRET

    def test_reads_file_and_strips(self, tmp_path):
        f = _key_file(tmp_path, SECRET + "\n")
        assert load_gemini_key(env={}, key_file=f) == SECRET

    def test_env_can_point_at_file(self, tmp_path):
        f = _key_file(tmp_path, SECRET)
        assert load_gemini_key(env={"GEMINI_API_KEY_FILE": str(f)}) == SECRET

    def test_group_readable_file_refused(self, tmp_path):
        f = _key_file(tmp_path, SECRET, mode=0o644)
        with pytest.raises(InsecureKeyFileError) as exc:
            load_gemini_key(env={}, key_file=f)
        assert SECRET not in str(exc.value)

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(MissingKeyError):
            load_gemini_key(env={}, key_file=tmp_path / "nope.txt")

    def test_empty_file_raises(self, tmp_path):
        f = _key_file(tmp_path, "  \n")
        with pytest.raises(MissingKeyError):
            load_gemini_key(env={}, key_file=f)
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/speech/realtime/test_keys.py -q -p no:cacheprovider`
Expected: FAIL — `ModuleNotFoundError: msb_v3.speech.realtime`

- [ ] **Step 3: Implement**

`src/msb_v3/speech/realtime/__init__.py`:
```python
"""Push-to-talk realtime voice (OpenAI Realtime / Gemini Live).

EXPERIMENTAL, like the rest of ``msb_v3.speech``. Provider SDKs, PyAudio and
AppKit are imported lazily so importing this package needs none of them.
"""
```

`src/msb_v3/speech/realtime/keys.py`:
```python
"""API key loading for the realtime voice providers.

Order: environment variable, then a file. Key text never appears in a log
line or an exception message — errors name the variable or path only.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Mapping, Optional

#: msb-v3 repo root: src/msb_v3/speech/realtime/keys.py → parents[4].
_REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_DOTENV = _REPO_ROOT / ".env"
DEFAULT_GEMINI_KEY_FILE = Path("~/.secrets/gemini-api-key.txt").expanduser()


class MissingKeyError(RuntimeError):
    """No usable key was found. The message names where we looked."""


class InsecureKeyFileError(MissingKeyError):
    """The key file is readable by group or others, so it is not used."""


def _dotenv_value(path: Path, name: str) -> Optional[str]:
    if not path.is_file():
        return None
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if line.startswith("export "):
            line = line[len("export "):]
        if not line.startswith(f"{name}="):
            continue
        value = line.split("=", 1)[1].strip().strip('"').strip("'")
        return value or None
    return None


def load_openai_key(
    env: Optional[Mapping[str, str]] = None,
    dotenv_path: Optional[Path] = None,
) -> str:
    """Return the OpenAI key from the environment or msb-v3's ``.env``."""
    env = os.environ if env is None else env
    value = (env.get("OPENAI_API_KEY") or "").strip()
    if value:
        return value
    path = dotenv_path or DEFAULT_DOTENV
    value = _dotenv_value(path, "OPENAI_API_KEY") or ""
    if value:
        return value
    raise MissingKeyError(f"OPENAI_API_KEY not set in the environment or {path}")


def _read_key_file(path: Path) -> str:
    if not path.is_file():
        raise MissingKeyError(f"Gemini key file not found: {path}")
    mode = path.stat().st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise InsecureKeyFileError(
            f"Gemini key file {path} is accessible by group/others; run: chmod 600 {path}"
        )
    value = path.read_text().strip()
    if not value:
        raise MissingKeyError(f"Gemini key file is empty: {path}")
    return value


def load_gemini_key(
    env: Optional[Mapping[str, str]] = None,
    key_file: Optional[Path] = None,
) -> str:
    """Return the Gemini key from ``GEMINI_API_KEY`` or the key file."""
    env = os.environ if env is None else env
    value = (env.get("GEMINI_API_KEY") or "").strip()
    if value:
        return value
    if key_file is None:
        override = (env.get("GEMINI_API_KEY_FILE") or "").strip()
        key_file = Path(override).expanduser() if override else DEFAULT_GEMINI_KEY_FILE
    return _read_key_file(key_file)
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/speech/realtime/test_keys.py -q -p no:cacheprovider`
Expected: 11 passed

---

### Task 3: PCM16 audio helpers

**Files:**
- Create: `src/msb_v3/speech/realtime/audio.py`
- Test: `tests/speech/realtime/test_audio.py`

**Interfaces:**
- Produces: `MIC_RATE = 16000`, `PROVIDER_OUT_RATE = 24000`, `pcm16_to_float(data: bytes) -> list[float]`, `resample_pcm16(data: bytes, src_rate: int, dst_rate: int) -> bytes`, `pcm16_seconds(data: bytes, rate: int) -> float`

- [ ] **Step 1: Write the failing tests**

```python
from __future__ import annotations

import numpy as np

from msb_v3.speech.realtime.audio import (
    MIC_RATE,
    pcm16_seconds,
    pcm16_to_float,
    resample_pcm16,
)


def _tone(seconds: float, rate: int) -> bytes:
    t = np.arange(int(seconds * rate)) / rate
    return (0.5 * np.sin(2 * np.pi * 440 * t) * 32767).astype("<i2").tobytes()


def test_seconds():
    assert pcm16_seconds(_tone(1.5, MIC_RATE), MIC_RATE) == 1.5


def test_seconds_empty():
    assert pcm16_seconds(b"", MIC_RATE) == 0.0


def test_to_float_range_and_length():
    data = _tone(0.1, MIC_RATE)
    floats = pcm16_to_float(data)
    assert len(floats) == len(data) // 2
    assert max(floats) <= 1.0 and min(floats) >= -1.0
    assert max(floats) > 0.45


def test_resample_16k_to_24k_length():
    out = resample_pcm16(_tone(1.0, 16000), 16000, 24000)
    assert len(out) // 2 == 24000


def test_resample_preserves_amplitude():
    out = np.frombuffer(resample_pcm16(_tone(0.5, 16000), 16000, 24000), dtype="<i2")
    assert 0.45 * 32767 < np.abs(out).max() <= 32767


def test_resample_same_rate_is_identity():
    data = _tone(0.2, 16000)
    assert resample_pcm16(data, 16000, 16000) == data


def test_resample_empty():
    assert resample_pcm16(b"", 16000, 24000) == b""
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/speech/realtime/test_audio.py -q -p no:cacheprovider`
Expected: FAIL — `ModuleNotFoundError: msb_v3.speech.realtime.audio`

- [ ] **Step 3: Implement**

```python
"""PCM16 helpers for the realtime path (mono, little-endian)."""

from __future__ import annotations

import numpy as np

MIC_RATE = 16000
PROVIDER_OUT_RATE = 24000


def pcm16_seconds(data: bytes, rate: int) -> float:
    """Duration of mono PCM16 ``data`` at ``rate``."""
    return (len(data) // 2) / rate


def pcm16_to_float(data: bytes) -> list[float]:
    """PCM16 bytes → floats in [-1, 1] (the ``AudioBuffer.samples`` shape)."""
    arr = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
    return arr.tolist()


def resample_pcm16(data: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Linear-interpolation resample. Good enough for speech to a speech API."""
    if src_rate == dst_rate or not data:
        return data
    src = np.frombuffer(data, dtype="<i2").astype(np.float32)
    n_out = int(round(len(src) * dst_rate / src_rate))
    x_old = np.arange(len(src))
    x_new = np.linspace(0, len(src) - 1, n_out)
    out = np.interp(x_new, x_old, src)
    return np.clip(out, -32768, 32767).astype("<i2").tobytes()
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/speech/realtime/test_audio.py -q -p no:cacheprovider`
Expected: 7 passed

---

### Task 4: Provider contract + tool registry

**Files:**
- Create: `src/msb_v3/speech/realtime/base.py`, `src/msb_v3/speech/realtime/tools.py`
- Test: `tests/speech/realtime/test_tools.py`

**Interfaces:**
- Produces (base.py):
  - `EventKind(str, Enum)`: `AUDIO="audio"`, `TRANSCRIPT="transcript"`, `TOOL_CALL="tool_call"`, `TURN_DONE="turn_done"`, `ERROR="error"`
  - `RealtimeEvent(kind: EventKind, audio: bytes = b"", text: str = "", call_id: str = "", name: str = "", arguments: dict = {})`
  - `RealtimeConfig(provider: str, model: str, instructions: str = DEFAULT_INSTRUCTIONS, voice: str | None = None, session_cap_seconds: float = 300.0)`
  - `DEFAULT_MODELS = {"openai": "gpt-realtime-2.1", "gemini": "gemini-3.1-flash-live-preview"}`
  - `ProviderError(RuntimeError)`
  - `RealtimeProvider` Protocol: `name: str`; `async connect() -> None`; `async send_audio(pcm16_16k: bytes) -> None`; `async end_turn() -> None`; `async interrupt() -> None`; `async send_tool_result(call_id: str, name: str, output: dict) -> None`; `events() -> AsyncIterator[RealtimeEvent]`; `async close() -> None`
- Produces (tools.py): `ToolSpec(name, description, parameters: dict, handler: Callable[[dict], dict], risk: RiskLevel = RiskLevel.LOW)`, `ToolRegistry(specs)` with `.specs`, `.openai_tools() -> list[dict]`, `.gemini_declarations() -> list[dict]`, `.dispatch(name: str, args: dict) -> dict`; `default_tools(base_url: str | None = None) -> ToolRegistry`

- [ ] **Step 1: Write the failing tests**

```python
from __future__ import annotations

from msb_v3.speech.realtime.tools import ToolRegistry, ToolSpec, default_tools
from msb_v3.speech.safety import RiskLevel


def _echo(args):
    return {"echo": args}


def _boom(args):
    raise ValueError("secret detail")


def _registry():
    return ToolRegistry([
        ToolSpec("echo", "Echo args.", {"type": "object", "properties": {}}, _echo),
        ToolSpec("boom", "Fails.", {"type": "object", "properties": {}}, _boom),
        ToolSpec("delete_all", "Dangerous.", {"type": "object", "properties": {}},
                 _echo, risk=RiskLevel.CRITICAL),
    ])


def test_dispatch_runs_low_risk_tool():
    assert _registry().dispatch("echo", {"a": 1}) == {"echo": {"a": 1}}


def test_unknown_tool_is_error():
    assert _registry().dispatch("nope", {}) == {"error": "unknown tool: nope"}


def test_non_low_risk_tool_refused_without_running():
    out = _registry().dispatch("delete_all", {})
    assert out == {"error": "delete_all is not allowed by voice policy (risk CRITICAL)"}


def test_handler_exception_is_contained():
    out = _registry().dispatch("boom", {})
    assert out == {"error": "boom failed: ValueError"}


def test_openai_tool_shape():
    tools = _registry().openai_tools()
    assert tools[0] == {
        "type": "function",
        "name": "echo",
        "description": "Echo args.",
        "parameters": {"type": "object", "properties": {}},
    }


def test_only_low_risk_tools_are_advertised():
    names = [t["name"] for t in _registry().openai_tools()]
    assert "delete_all" not in names
    assert [d["name"] for d in _registry().gemini_declarations()] == names


def test_default_tools_time():
    reg = default_tools()
    out = reg.dispatch("get_current_time", {})
    assert "time" in out and "date" in out


def test_default_tools_status_unreachable_is_error_not_crash():
    reg = default_tools(base_url="http://127.0.0.1:9")
    out = reg.dispatch("get_system_status", {})
    assert out["error"].startswith("msb-v3 not reachable")
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/speech/realtime/test_tools.py -q -p no:cacheprovider`
Expected: FAIL — `ModuleNotFoundError: msb_v3.speech.realtime.tools`

- [ ] **Step 3: Implement `base.py`**

```python
"""Provider-neutral contract for realtime speech-to-speech sessions."""

from __future__ import annotations

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
```

- [ ] **Step 4: Implement `tools.py`**

```python
"""Tools the realtime model may call. Only LOW-risk, read-only tools run."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, List, Optional, Sequence

from msb_v3.speech.safety import RiskLevel


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict
    handler: Callable[[dict], dict]
    risk: RiskLevel = RiskLevel.LOW


class ToolRegistry:
    def __init__(self, specs: Sequence[ToolSpec]) -> None:
        self.specs: Dict[str, ToolSpec] = {s.name: s for s in specs}

    def _allowed(self) -> List[ToolSpec]:
        return [s for s in self.specs.values() if s.risk is RiskLevel.LOW]

    def openai_tools(self) -> List[dict]:
        return [
            {"type": "function", "name": s.name, "description": s.description,
             "parameters": s.parameters}
            for s in self._allowed()
        ]

    def gemini_declarations(self) -> List[dict]:
        return [
            {"name": s.name, "description": s.description, "parameters": s.parameters}
            for s in self._allowed()
        ]

    def dispatch(self, name: str, args: dict) -> dict:
        spec = self.specs.get(name)
        if spec is None:
            return {"error": f"unknown tool: {name}"}
        if spec.risk is not RiskLevel.LOW:
            return {"error": f"{name} is not allowed by voice policy (risk {spec.risk.value})"}
        try:
            return spec.handler(args)
        except Exception as exc:  # noqa: BLE001 — a tool failure must become a spoken error, not a crash
            return {"error": f"{name} failed: {type(exc).__name__}"}


_EMPTY = {"type": "object", "properties": {}}


def _current_time(_args: dict) -> dict:
    now = datetime.now().astimezone()
    return {"time": now.strftime("%-I:%M %p"), "date": now.strftime("%A, %B %-d, %Y"),
            "timezone": now.strftime("%Z")}


def _make_status(base_url: str) -> Callable[[dict], dict]:
    def _status(_args: dict) -> dict:
        url = base_url.rstrip("/") + "/health"
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:  # noqa: S310 — local URL
                body = resp.read(4096).decode("utf-8", "replace")
        except (urllib.error.URLError, OSError) as exc:
            return {"error": f"msb-v3 not reachable at {base_url} ({type(exc).__name__})"}
        try:
            return {"status": json.loads(body)}
        except json.JSONDecodeError:
            return {"status": body[:500]}
    return _status


def default_tools(base_url: Optional[str] = None) -> ToolRegistry:
    base = base_url or os.environ.get("MSB_BASE_URL", "http://127.0.0.1:8766")
    return ToolRegistry([
        ToolSpec("get_current_time", "Get the current local date and time.", _EMPTY, _current_time),
        ToolSpec("get_system_status", "Get the msb-v3 system health status.", _EMPTY,
                 _make_status(base)),
    ])
```

- [ ] **Step 5: Run to verify pass**

Run: `python3 -m pytest tests/speech/realtime/test_tools.py -q -p no:cacheprovider`
Expected: 8 passed

---

### Task 5: OpenAI Realtime adapter

**Files:**
- Create: `src/msb_v3/speech/realtime/openai_rt.py`
- Test: `tests/speech/realtime/test_openai_rt.py`

**Interfaces:**
- Consumes: `RealtimeConfig`, `RealtimeEvent`, `EventKind`, `ProviderError` (base), `ToolRegistry` (tools), `resample_pcm16`, `MIC_RATE` (audio)
- Produces: `OpenAIRealtimeProvider(config: RealtimeConfig, api_key: str, tools: ToolRegistry | None = None, connection_factory: Callable[[], AsyncContextManager] | None = None)`, `name = "openai"`

SDK facts (verified 2026-09-23 against openai 2.53.0): `AsyncOpenAI(api_key=...).realtime.connect(model=...)` is an async context manager yielding a connection with `session.update(session=...)`, `input_audio_buffer.append(audio=b64)`, `input_audio_buffer.commit()`, `response.create()`, `response.cancel()`, `conversation.item.create(item=...)`, async iteration over server events. Server event types: `response.output_audio.delta` (`.delta` b64), `response.output_audio_transcript.delta` (`.delta`), `response.function_call_arguments.done` (`.call_id`, `.name`, `.arguments` JSON), `response.done`, `error` (`.error.message`).

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/speech/realtime/test_openai_rt.py -q -p no:cacheprovider`
Expected: FAIL — `ModuleNotFoundError: msb_v3.speech.realtime.openai_rt`

- [ ] **Step 3: Implement**

```python
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
            raise ProviderError(f"openai realtime connect failed ({type(exc).__name__})") from None

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
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/speech/realtime/test_openai_rt.py -q -p no:cacheprovider`
Expected: 8 passed

---

### Task 6: Gemini Live adapter

**Files:**
- Create: `src/msb_v3/speech/realtime/gemini_live.py`
- Test: `tests/speech/realtime/test_gemini_live.py`

**Interfaces:**
- Consumes: same as Task 5.
- Produces: `GeminiLiveProvider(config, api_key, tools=None, session_factory: Callable[[], AsyncContextManager] | None = None)`, `name = "gemini"`

SDK facts (verified against google-genai 2.12.1): `genai.Client(api_key=...).aio.live.connect(model=, config=types.LiveConnectConfig(...))` async context manager → session with `send_realtime_input(audio=Blob|activity_start=|activity_end=)`, `send_tool_response(function_responses=[FunctionResponse(id, name, response)])`, `receive()` async iterator of `LiveServerMessage` (`.server_content.model_turn.parts[].inline_data.data`, `.server_content.output_transcription.text`, `.server_content.turn_complete`, `.server_content.interrupted`, `.tool_call.function_calls[]` with `.id .name .args`). `receive()` stops after `turn_complete`, so `events()` loops over it.

Interrupt: with manual activity detection there is no cancel call; `interrupt()` marks local playback-drop until the current turn completes (the next `activity_start` interrupts server-side generation by default).

- [ ] **Step 1: Write the failing tests**

```python
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
        if e is not None and e.kind is EventKind.TURN_DONE:
            break
    assert got == [EventKind.TURN_DONE]


@pytest.mark.asyncio
async def test_connect_failure_is_provider_error_without_key():
    p, _ = _provider(fail=True)
    with pytest.raises(ProviderError) as exc:
        await p.connect()
    assert KEY not in str(exc.value)
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/speech/realtime/test_gemini_live.py -q -p no:cacheprovider`
Expected: FAIL — `ModuleNotFoundError: msb_v3.speech.realtime.gemini_live`

- [ ] **Step 3: Implement**

```python
"""Gemini Live adapter (WebSocket via google-genai)."""

from __future__ import annotations

from typing import Any, AsyncContextManager, AsyncIterator, Callable, Optional

from msb_v3.speech.realtime.base import (
    EventKind,
    ProviderError,
    RealtimeConfig,
    RealtimeEvent,
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
        tools = None
        if self.tools and self.tools.gemini_declarations():
            tools = [types.Tool(function_declarations=[
                types.FunctionDeclaration(**d) for d in self.tools.gemini_declarations()])]
        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
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
            raise ProviderError(f"gemini live connect failed ({type(exc).__name__})") from None

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
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/speech/realtime/test_gemini_live.py -q -p no:cacheprovider`
Expected: 6 passed

---

### Task 7: Push-to-talk controller (the core invariant)

**Files:**
- Create: `src/msb_v3/speech/realtime/talk.py`
- Test: `tests/speech/realtime/test_talk.py`

**Interfaces:**
- Consumes: `RealtimeProvider`, `RealtimeEvent`, `EventKind`, `ProviderError` (base); `ToolRegistry` (tools); `pcm16_to_float`, `pcm16_seconds`, `MIC_RATE` (audio); `NotEnrolledError`, `SpeakerVerifying` (gate); `AudioBuffer` (models)
- Produces:
  - `AudioSink` Protocol: `play(pcm16_24k: bytes) -> None`, `stop() -> None`
  - `TurnResult(sent: bool, reason: str = "", speaker_id: str = "unknown", confidence: float = 0.0, seconds_sent: float = 0.0, transcript: str = "")`
  - `PushToTalkController(provider_factory: Callable[[], RealtimeProvider], verifier, sink: AudioSink, *, tools: ToolRegistry | None = None, speak_local: Callable[[str], None] = say, verify_seconds: float = 1.5, session_cap_seconds: float = 300.0, usage_log: Path | None = None, clock: Callable[[], float] = time.monotonic)`
    - `async run_turn(frames: AsyncIterator[bytes]) -> TurnResult` — `frames` yields 16 kHz PCM16 chunks until the button ends the turn.
    - `interrupt() -> None` — sync; stops playback and flags the provider interrupt for the running reply.
    - `async close() -> None`
  - `say(text: str) -> None` — macOS `say`, non-blocking.

Behaviour:
1. `run_turn` first checks `verifier.list_speakers()`; empty → `NotEnrolledError` before reading any frame.
2. Buffer frames until `verify_seconds` of audio (or frames end). Verify the buffer (`AudioBuffer(samples=pcm16_to_float(buf), sample_rate=16000)`). Error/unenrolled → drain remaining frames, discard, return `TurnResult(sent=False, reason="unverified speaker"|"verification error")`. No provider is created.
3. Verified → ensure session (create via factory + `connect()` if none or cap exceeded; on cap exceeded close old one and `speak_local("Session limit reached, starting fresh.")`). Send buffer, then stream the remaining frames, then `end_turn()`.
4. Consume `provider.events()`: AUDIO → `sink.play`; TRANSCRIPT → append; TOOL_CALL → `tools.dispatch` → `send_tool_result`, mark pending; TURN_DONE → stop if no tool call was dispatched since the last TURN_DONE, else reset the flag and keep reading; ERROR → record reason, stop. If `interrupt()` was called, call `provider.interrupt()` once and stop playback.
5. Any `ProviderError`/`OSError` during connect/send/receive → `speak_local("I'm offline.")`, close the session, return `TurnResult(sent=<whether any audio was sent>, reason="offline: <ExcName>")`.
6. Append usage `{date, provider, seconds_sent}` to `usage_log` when `seconds_sent > 0`.

- [ ] **Step 1: Write the failing tests**

```python
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
    ctl, _, _ = _ctl(v, verify_seconds=0.5)
    await ctl.run_turn(frames(30))
    assert v.calls == 1


@pytest.mark.asyncio
async def test_verified_sends_buffer_then_live_frames_in_order():
    p = FakeProvider()
    ctl, _, _ = _ctl(provider=p, verify_seconds=0.5)
    r = await ctl.run_turn(frames(12))
    assert r.sent is True and r.seconds_sent == pytest.approx(1.2)
    assert b"".join(p.sent) == CHUNK * 12
    assert p.log[:1] == ["connect"] and "end_turn" in p.log


@pytest.mark.asyncio
async def test_short_turn_under_verify_window_still_verified_then_sent():
    p = FakeProvider()
    ctl, _, _ = _ctl(provider=p, verify_seconds=1.5)
    r = await ctl.run_turn(frames(5))
    assert r.sent is True and b"".join(p.sent) == CHUNK * 5


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
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/speech/realtime/test_talk.py -q -p no:cacheprovider`
Expected: FAIL — `ModuleNotFoundError: msb_v3.speech.realtime.talk`

- [ ] **Step 3: Implement**

```python
"""Push-to-talk controller: verify locally, then (and only then) talk to the cloud.

Invariant: no audio reaches a provider until the speaker is verified. The
provider is not even created for an unverified turn.
"""

from __future__ import annotations

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
        session_cap_seconds: float = 300.0,
        usage_log: Optional[Path] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.provider_factory = provider_factory
        self.verifier = verifier
        self.sink = sink
        self.tools = tools
        self.speak_local = speak_local
        self.verify_seconds = verify_seconds
        self.session_cap_seconds = session_cap_seconds
        self.usage_log = usage_log
        self.clock = clock
        self._provider: Optional[RealtimeProvider] = None
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

        # 2. Verify. Fail closed; nothing has been sent and no provider exists.
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
        except (ProviderError, OSError) as exc:
            result.reason = f"offline: {type(exc).__name__}"
            self.speak_local(OFFLINE_NOTICE)
            await self._drop_session()
        finally:
            result.seconds_sent = seconds
            self._record_usage(seconds)
        return result

    # ── internals ──────────────────────────────────────────────────────

    async def _drain(self, frames: AsyncIterator[bytes], exhausted: bool) -> None:
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
            await provider.connect()
            self._opened_at = self.clock()
        return self._provider

    async def _drop_session(self) -> None:
        await self.close()

    async def _consume_reply(self, provider: RealtimeProvider, result: TurnResult) -> None:
        tool_pending = False
        text: List[str] = []
        async for ev in provider.events():
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
        name = getattr(self._provider, "name", None) or "unknown"
        row = {"date": date.today().isoformat(), "provider": name,
               "seconds_sent": round(seconds, 3)}
        self.usage_log.parent.mkdir(parents=True, exist_ok=True)
        with self.usage_log.open("a") as fh:
            fh.write(json.dumps(row) + "\n")
```

Note for implementer: `_record_usage` reads the provider name before `_drop_session` may clear it; in the offline path `seconds` is 0 unless audio was already sent, and the name falls back to `"unknown"` — acceptable. The `test_interrupt…` test expects exactly `[b"a"]` played: the check at the top of the loop runs before the second AUDIO event is handled.

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/speech/realtime/test_talk.py -q -p no:cacheprovider`
Expected: 16 passed

---

### Task 8: Button, audio I/O, CLI

**Files:**
- Create: `src/msb_v3/speech/realtime/button.py`, `src/msb_v3/speech/realtime/audio_io.py`, `src/msb_v3/speech/realtime/cli.py`
- Test: `tests/speech/realtime/test_button.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `parse_media_key(data1: int) -> tuple[int, bool]` (key_code, is_down); `PLAY_PAUSE_KEYCODE = 16`; `EnterKeyButton` with `async wait_press() -> None`; `MediaKeyButton` with `async wait_press() -> None` (AppKit global monitor in a thread, pushes into an `asyncio.Queue`); `MicStream(rate=16000, chunk_frames=1600)` with `start()`, `stop()`, `async frames_until(stop: asyncio.Event) -> AsyncIterator[bytes]`; `Speaker(rate=24000)` implementing `AudioSink`; `main(argv: list[str] | None = None) -> int`.

- [ ] **Step 1: Write the failing test**

```python
from msb_v3.speech.realtime.button import PLAY_PAUSE_KEYCODE, parse_media_key


def test_parse_play_pause_down():
    data1 = (PLAY_PAUSE_KEYCODE << 16) | (0xA << 8)
    assert parse_media_key(data1) == (16, True)


def test_parse_play_pause_up():
    data1 = (PLAY_PAUSE_KEYCODE << 16) | (0xB << 8)
    assert parse_media_key(data1) == (16, False)
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/speech/realtime/test_button.py -q -p no:cacheprovider`
Expected: FAIL — module not found

- [ ] **Step 3: Implement `button.py`**

```python
"""Talk-button sources: Enter key (no permissions) or a headset media key."""

from __future__ import annotations

import asyncio
import threading
from typing import Optional, Tuple

PLAY_PAUSE_KEYCODE = 16
_NS_SYSTEM_DEFINED = 14
_MEDIA_KEY_SUBTYPE = 8


def parse_media_key(data1: int) -> Tuple[int, bool]:
    """Decode NSEvent.data1 of a system-defined media-key event."""
    key_code = (data1 & 0xFFFF0000) >> 16
    is_down = ((data1 & 0xFF00) >> 8) == 0xA
    return key_code, is_down


class EnterKeyButton:
    """Press Enter in the terminal. Needs no macOS permission."""

    async def wait_press(self) -> None:
        await asyncio.get_running_loop().run_in_executor(None, input)


class MediaKeyButton:
    """Headset play/pause via an AppKit global monitor (needs Input Monitoring)."""

    def __init__(self, key_code: int = PLAY_PAUSE_KEYCODE) -> None:
        self.key_code = key_code
        self._queue: "asyncio.Queue[None]" = asyncio.Queue()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None

    def _run(self) -> None:
        from AppKit import NSApplication, NSEvent, NSSystemDefinedMask
        from PyObjCTools import AppHelper

        def handler(event):  # type: ignore[no-untyped-def]
            if event.type() != _NS_SYSTEM_DEFINED or event.subtype() != _MEDIA_KEY_SUBTYPE:
                return
            code, down = parse_media_key(event.data1())
            if code == self.key_code and down and self._loop is not None:
                self._loop.call_soon_threadsafe(self._queue.put_nowait, None)

        NSApplication.sharedApplication()
        NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(NSSystemDefinedMask, handler)
        AppHelper.runConsoleEventLoop()

    async def wait_press(self) -> None:
        if self._thread is None:
            self._loop = asyncio.get_running_loop()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        await self._queue.get()
```

- [ ] **Step 4: Implement `audio_io.py`**

```python
"""PyAudio microphone stream and speaker for the realtime path (live-tested)."""

from __future__ import annotations

import asyncio
import queue
import threading
from typing import Any, AsyncIterator, Optional

from msb_v3.speech.realtime.audio import MIC_RATE, PROVIDER_OUT_RATE


class MicStream:
    def __init__(self, rate: int = MIC_RATE, chunk_frames: int = 1600) -> None:
        self.rate, self.chunk_frames = rate, chunk_frames
        self._pa: Any = None
        self._stream: Any = None

    def start(self) -> None:
        import pyaudio

        self._pa = pyaudio.PyAudio()
        self._stream = self._pa.open(format=pyaudio.paInt16, channels=1, rate=self.rate,
                                     input=True, frames_per_buffer=self.chunk_frames)

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop_stream()
            self._stream.close()
        if self._pa is not None:
            self._pa.terminate()
        self._stream = self._pa = None

    async def frames_until(self, stop: asyncio.Event) -> AsyncIterator[bytes]:
        loop = asyncio.get_running_loop()
        while not stop.is_set():
            data = await loop.run_in_executor(
                None, lambda: self._stream.read(self.chunk_frames, exception_on_overflow=False))
            yield data


class Speaker:
    """Plays PCM16 24 kHz on a background thread; ``stop()`` flushes."""

    def __init__(self, rate: int = PROVIDER_OUT_RATE) -> None:
        import pyaudio

        self._pa = pyaudio.PyAudio()
        self._stream = self._pa.open(format=pyaudio.paInt16, channels=1, rate=rate, output=True)
        self._q: "queue.Queue[Optional[bytes]]" = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while True:
            data = self._q.get()
            if data is None:
                return
            self._stream.write(data)

    def play(self, pcm16_24k: bytes) -> None:
        self._q.put(pcm16_24k)

    def stop(self) -> None:
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass

    def close(self) -> None:
        self._q.put(None)
        self._thread.join(timeout=2)
        self._stream.close()
        self._pa.terminate()
```

- [ ] **Step 5: Implement `cli.py`**

```python
"""Push-to-talk CLI.

    python -m msb_v3.speech.realtime.cli --provider openai --enrollments PATH
    python -m msb_v3.speech.realtime.cli --provider gemini --button media --enrollments PATH

Press the button (Enter by default) to start talking, press again to finish.
Pressing while it is replying interrupts it. Ctrl+C quits.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path
from typing import List, Optional

from msb_v3.speech.realtime.base import RealtimeConfig
from msb_v3.speech.realtime.keys import MissingKeyError, load_gemini_key, load_openai_key
from msb_v3.speech.realtime.tools import default_tools

DEFAULT_USAGE_LOG = Path("~/.msb/realtime-usage.jsonl").expanduser()


def _provider_factory(name: str, model: Optional[str]):
    config = RealtimeConfig.for_provider(name, model)
    tools = default_tools()
    if name == "openai":
        from msb_v3.speech.realtime.openai_rt import OpenAIRealtimeProvider

        key = load_openai_key()
        return lambda: OpenAIRealtimeProvider(config, key, tools=tools)
    from msb_v3.speech.realtime.gemini_live import GeminiLiveProvider

    key = load_gemini_key()
    return lambda: GeminiLiveProvider(config, key, tools=tools)


async def _loop(args: argparse.Namespace) -> None:
    from msb_v3.speech.realtime.audio_io import MicStream, Speaker
    from msb_v3.speech.realtime.button import EnterKeyButton, MediaKeyButton
    from msb_v3.speech.realtime.talk import PushToTalkController
    from msb_v3.speech.speaker import SpeakerVerifier

    verifier = SpeakerVerifier(enrollments_path=args.enrollments)
    speaker = Speaker()
    ctl = PushToTalkController(_provider_factory(args.provider, args.model), verifier, speaker,
                               tools=default_tools(), usage_log=DEFAULT_USAGE_LOG)
    button = MediaKeyButton() if args.button == "media" else EnterKeyButton()
    mic = MicStream()
    print(f"[{args.provider}] press the button to talk, press again to finish. Ctrl+C quits.")
    try:
        while True:
            await button.wait_press()
            ctl.interrupt()  # a press during a reply cuts it off
            stop = asyncio.Event()
            mic.start()
            print("  listening…")

            async def end_on_press() -> None:
                await button.wait_press()
                stop.set()

            ender = asyncio.create_task(end_on_press())
            result = await ctl.run_turn(mic.frames_until(stop))
            ender.cancel()
            mic.stop()
            print(f"  sent={result.sent} reason={result.reason or 'ok'} "
                  f"voice={result.confidence:.2f} seconds={result.seconds_sent:.1f} "
                  f"reply={result.transcript!r}")
    finally:
        await ctl.close()
        speaker.close()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="msb-voice")
    parser.add_argument("--provider", choices=["openai", "gemini"], default="openai")
    parser.add_argument("--model", default=None)
    parser.add_argument("--button", choices=["enter", "media"], default="enter")
    parser.add_argument("--enrollments", required=True, help="SpeakerVerifier enrollments JSON")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        asyncio.run(_loop(args))
    except MissingKeyError as exc:
        print(f"error: {exc}")
        return 2
    except KeyboardInterrupt:
        print("\nbye")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Note: with the Enter button, the first press is the Enter that `wait_press` consumes; the second Enter ends the turn. A press during a reply is only seen at the next `wait_press`, so interruption with Enter happens at the start of the next turn (acceptable for v1; the media-key button behaves the same way).

- [ ] **Step 6: Run to verify pass**

Run: `python3 -m pytest tests/speech/realtime/test_button.py -q -p no:cacheprovider`
Expected: 2 passed

---

### Task 9: Whole-suite checks and live sessions

- [ ] **Step 1: Realtime + speech suites**

Run: `python3 -m pytest tests/speech tests/wake -q -p no:cacheprovider`
Expected: all pass (239 existing + 57 new).

- [ ] **Step 2: Lint and types**

Run: `python3 -m ruff check src/msb_v3/speech tests/speech && python3 -m mypy src/msb_v3/speech`
Expected: `All checks passed!` and `Success: no issues found`. Fix any finding before continuing.

- [ ] **Step 3: No Whisper on this path**

Run: `python3 -c "import sys, msb_v3.speech.realtime.cli, msb_v3.speech.realtime.talk; print('whisper' in sys.modules)"`
Expected: `False`

- [ ] **Step 4: Live, OpenAI (Wilson on the headset)**

Unload idle Ollama models first (`ollama ps`, `ollama stop <model>`). Then:
Run: `python3 -m msb_v3.speech.realtime.cli --provider openai --enrollments <scratchpad>/voice_enrollment.json`
Try: "what time is it", "what's the system status", then a second person asks something.
Expected: owner turns `sent=True` with a spoken reply; second person `sent=False reason=unverified speaker`. Record time to first audio.

- [ ] **Step 5: Live, Gemini** — same with `--provider gemini`.

- [ ] **Step 6: Record results** in the spec's open items and the vault note `20_Research/Voice-Agent-Deep-Research-Brief.md`. Do not commit.
