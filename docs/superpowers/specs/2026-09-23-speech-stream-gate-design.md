# Speech stream gate — speaker-verified always-on mode + sleep/wake

**Date:** 2026-09-23
**Status:** approved design, not built
**Scope:** `src/msb_v3/speech/` (EXPERIMENTAL subsystem, outside the release contract)
**Parent blueprint:** vault `30_Architecture/Voice-Identity-Aware-Workstation.md` (MVP-2 slice)

## Problem

`VoiceStream` (always-on mode) goes wake word → command → `VoiceResponder`
without ever checking who is speaking. Speaker verification exists
(`SpeakerVerifier`, resemblyzer) but is only called from `SpeechPipeline`,
which is a separate path. Also:

- The default wake patterns match a bare `msb` / `sovereign`, so ordinary
  conversation can trigger the agent.
- `VoiceStreamConfig.wake_words` is never read; `listen_once` builds a fresh
  `WakeWordDetector()` with defaults on every utterance.
- There is no way to put the agent to sleep by voice. The loop only ends at
  `max_iterations` or via `stop()`.

Goal: hands-free use in a loud environment (noise-cancelling headset), where
only the enrolled owner can command, sleep, or wake the agent.

## Decisions (made by Wilson, 2026-09-23)

1. Unverified speaker with a valid wake phrase → **ignore silently**, log it.
2. Nobody enrolled → always-on mode **refuses to start** with a clear error.
3. Phrases: wake word requires the `hey` prefix (`hey sovereign`, `hey msb`).
   Commands: `hey sovereign, sleep` and `hey sovereign, wake up`. Phrases are
   configurable.

## Design

### New unit: `speech/gate.py`

Pure decision logic. No microphone, no transcription, no TTS.

```python
class GateAction(str, Enum):
    IGNORE = "IGNORE"
    SLEEP = "SLEEP"
    WAKE = "WAKE"
    COMMAND = "COMMAND"

@dataclass
class GateDecision:
    action: GateAction
    command_text: str = ""
    reason: str = ""
    speaker_id: str = "unknown"
    speaker_confidence: float = 0.0

class StreamGate:
    def __init__(self, verifier, wake_words=None,
                 sleep_phrases=("sleep", "go to sleep"),
                 wake_phrases=("wake up", "wake")) -> None: ...
    @property
    def asleep(self) -> bool: ...
    def decide(self, text: str, audio: AudioBuffer) -> GateDecision: ...
```

`verifier` is anything with `verify(audio) -> SpeakerIdentity` and
`list_speakers() -> list[str]` (duck-typed so tests use a fake).

`decide` evaluates in this order:

1. **Wake phrase check** (`WakeWordDetector(require_prefix=True)`).
   Not found → `IGNORE`, reason `no wake word`. The verifier is not called.
2. **Speaker verification.** `verifier.verify(audio)`. If it raises, or
   `is_enrolled` is false → `IGNORE`, reason `unverified speaker` (includes
   confidence). Logged at INFO via the module logger. Fail-closed.
3. **Sleep phrase** (command text, normalized: lowercase, trailing
   punctuation stripped, exact match against `sleep_phrases`) → set asleep,
   `SLEEP`. Sleeping while already asleep is still `SLEEP` (idempotent).
4. **Asleep:** wake phrase → set awake, `WAKE`. Anything else → `IGNORE`,
   reason `asleep`.
5. **Awake, empty command** → `IGNORE`, reason `wake word without command`.
6. **Otherwise** → `COMMAND` with the command text.

Every decision carries `speaker_id` / `speaker_confidence` once step 2 ran.
The raw embedding is never copied into the decision.

Exact-match on sleep/wake is deliberate: "sleep through the alarm" must not
put the agent to sleep.

### Change: `WakeWordDetector`

Add `require_prefix: bool = False`. When true, the pattern is
`\bhey\s+<word>\b` (no bare form). Default stays false so the existing 19
wakeword tests and other callers are unchanged.

### Change: `VoiceStream`

- `__init__(config=None, verifier=None)`. `verifier` defaults to a
  `SpeakerVerifier(enrollments_path=config.enrollments_path)`; add
  `enrollments_path: Optional[str] = None` to `VoiceStreamConfig`.
- Build one `StreamGate` in `__init__`, honoring `config.wake_words`
  (default `["sovereign", "msb"]`).
- `listen_once` and `listen_continuous` raise `NotEnrolledError`
  (new, subclass of `RuntimeError`, defined in `gate.py`) when
  `verifier.list_speakers()` is empty, before touching the microphone.
- `listen_once` after transcription calls `gate.decide(text, audio)`:
  - `IGNORE` → state `WAITING`, `error` = reason.
  - `SLEEP` / `WAKE` → state `WAITING`; if `speak_aloud`, speak
    "Going to sleep." / "I'm awake." through the responder's existing
    `VoiceResponder._render_response(text)` (the audio-renderer seam).
  - `COMMAND` → existing `respond_with_session` path, unchanged.
- `StreamResult` gains `action: str = ""`, `speaker_id: str = ""`,
  `speaker_confidence: float = 0.0`, included in `as_dict()`.
- New read-only property `VoiceStream.asleep` delegating to the gate.

Sleep state is in-memory only. Restarting the stream starts awake.

## Testing

- `tests/speech/test_speech_gate.py`: full decision table with a fake
  verifier (verified / unverified / raises) — no resemblyzer, no mic, no
  webrtcvad import required. Cases: no wake word (verifier not called),
  bare `msb` ignored, stranger ignored, verifier exception ignored, sleep,
  sleep idempotent, asleep ignores commands, stranger cannot wake, owner
  wakes, "sleep through the alarm" is a COMMAND, empty command ignored,
  custom phrases.
- `tests/speech/test_speech_wakeword.py`: add `require_prefix` cases.
- `tests/speech/test_speech_stream.py`: `NotEnrolledError` when empty
  verifier; `listen_once` with capture/transcribe monkeypatched for
  IGNORE / SLEEP / WAKE / COMMAND. The existing mic-touching
  `test_listen_once_returns_result` gets an enrolled fake verifier so it
  still runs.

Test-first: each test is written and seen failing before the code.

## Out of scope (known gaps, recorded here on purpose)

- Noise-aware confidence and PIN / non-voice fallback (next slice).
- `SpeechPipeline._authorize` still allows everyone when nobody is enrolled
  ("demo mode"). Fail-open; not touched here.
- Spoof resistance. A 0.75 resemblyzer threshold identifies a speaker; it
  does not reject a replayed recording or synthetic voice.
- A dedicated low-power wake-word model (detection is still whisper-tiny +
  text match).
- Sleep state persistence across restarts.
