# Push-to-talk realtime voice (OpenAI Realtime + Gemini Live)

**Date:** 2026-09-23
**Status:** approved design, not built
**Scope:** `src/msb_v3/speech/` (EXPERIMENTAL subsystem, outside the release contract)
**Builds on:** `2026-09-23-speech-stream-gate-design.md` (reuses `SpeakerVerifier`)

## Why

A measured baseline (34 recorded clips, 2026-09-23) showed the local path's
weak link: Whisper-based wake-word matching found the wake phrase 67% of the
time (95% CI 47-82%) on tiny and 75% on base. Speaker verification passed the
owner 24/24. Local models on a 16 GB Mac mini can't match cloud realtime
voice for understanding or conversational speed.

Decision (Wilson, 2026-09-23): use cloud realtime voice APIs for everything
after the owner is verified, started by a **headset button** (push-to-talk),
building **OpenAI Realtime and Gemini Live** adapters from the start. This
replaces the earlier "cloud as opt-in, local default" decision: cloud becomes
the default conversation path. Verification stays local.

## Provider facts (from docs, 2026-09-23; re-verify at build time)

| | OpenAI Realtime | Gemini Live |
|---|---|---|
| Model (config default) | `gpt-realtime-2.1` | `gemini-3.1-flash-live-preview` (also listed: `gemini-3.8-live`) |
| Transport | WebSocket (server) or WebRTC | WebSocket (`client.aio.live.connect`) |
| Input audio | PCM16 LE, mono, **24 kHz** | PCM16 LE, **16 kHz** (`audio/pcm;rate=16000`) |
| Output audio | PCM16 24 kHz | PCM16 24 kHz |
| Tools | function calling | function calling (async/blocking modes) |
| Limits | — | audio-only session 15 min |

Docs were read through a summarizing fetch tool; exact event names and field
names are confirmed against the installed SDKs (`openai` 2.53.0,
`google-genai` 2.12.1) during implementation.

## Flow

```
button press → capture mic (16 kHz) → buffer first VERIFY_SECONDS (1.5 s)
  → SpeakerVerifier.verify(buffer)
      ├─ unverified / error / nobody enrolled → discard buffer, send nothing,
      │   stay silent, log reason + confidence
      └─ verified → open provider session → send buffer, then stream live
         → play reply audio (24 kHz)
button press during reply → interrupt playback (barge-in) and start a new turn
button press while talking → end the turn
```

Turn boundaries are button-driven (server VAD disabled / manual activity
signals) so the API never decides on its own that a stranger's speech is a turn.

## Units

1. **`speech/realtime/base.py`** — `RealtimeProvider` Protocol:
   `async connect()`, `async send_audio(pcm16: bytes)`, `async end_turn()`,
   `async interrupt()`, `async close()`, `events()` → async iterator of
   `RealtimeEvent` (`kind`: `audio` | `transcript` | `tool_call` | `turn_done`
   | `error`; plus payload). Also `RealtimeConfig` (provider, model id,
   voice, session cap seconds, instructions).
2. **`speech/realtime/openai_rt.py`** — OpenAI adapter. Resamples 16 kHz →
   24 kHz before sending.
3. **`speech/realtime/gemini_live.py`** — Gemini adapter.
4. **`speech/talk.py`** — `PushToTalkController`: owns buffering, the verify
   decision, send-or-discard, playback, session cap, usage tally. Depends only
   on the Protocol, a verifier, and injectable audio in/out, so it is fully
   testable offline.
5. **`speech/button.py`** — talk-button listener. Step one is a spike that
   records what the owner's Bluetooth headset button actually emits on macOS
   (media key vs. call-control vs. nothing). Keyboard hotkey is always
   available as fallback. Any macOS permission (Accessibility / Input
   Monitoring) is granted by Wilson.
6. **Tools** — 2-3 read-only tools only (system status, current time),
   dispatched through the existing speech safety gate. The ~50-task catalog is
   a separate spec.

## Guardrails

- **No audio leaves the Mac before verification passes.** This is the
  primary invariant and has dedicated tests.
- Nobody enrolled → controller refuses to start (`NotEnrolledError`, reused).
- API keys: `OPENAI_API_KEY` from `msb-v3/.env` (already present). Gemini:
  `GEMINI_API_KEY` env var if set, otherwise the file named by
  `GEMINI_API_KEY_FILE`, default `~/.secrets/gemini-api-key.txt` (one line,
  raw key, mode 600). The key is not copied into `.env`. The loader strips
  whitespace, refuses a file readable by group/others, and never logs or
  includes key text in errors.
- Session cap (default 300 s); on cap the session closes with a short spoken
  notice.
- Usage tally: seconds of audio sent per provider per day, appended to a local
  JSONL, so cost is visible.
- Network/provider failure → local macOS voice says "I'm offline", no retry
  loop.
- Missing key for a provider → that provider is unavailable with a clear
  error; the other still works.
- This path loads no Whisper model; only resemblyzer for verification.

## Testing

- **Controller (offline, fake provider + fake verifier + fake audio):**
  unverified → zero `send_audio` calls; verifier raises → zero calls; nobody
  enrolled → refuses; verified → buffer sent first then live frames, in order;
  button during reply → `interrupt()`; session cap closes; usage tally written;
  provider error → offline notice, no crash.
- **Adapters (offline):** feed recorded/synthetic provider event sequences
  through a fake transport; assert audio resampling (OpenAI 24 kHz), event
  mapping to `RealtimeEvent`, tool-call mapping, and that keys never appear in
  raised error text.
- **Live (manual, owner on headset):** one short session per provider; record
  time-to-first-audio and whether a second person's voice is refused.

Test-first: each test written and seen failing before the code.

## Out of scope

- The ~50-task catalog and any tool that writes, sends, deletes, or spends.
- Trained local wake word (kept as a later hands-free option).
- Anti-spoofing: a recording of the owner still passes verification.
- WebRTC transport, noise suppression, persisted conversation memory.
- Changing `SpeechPipeline` demo mode (still fail-open; noted in the gate spec).

## Privacy and cost notes

- Verified owner audio is sent to OpenAI or Google. Bystanders' speech inside a
  verified turn is also sent; push-to-talk keeps that window short.
- Both providers bill per audio minute; check current pricing before regular use.
