# Speech Subsystem — Architecture Map

**Date**: 2026-08-27
**Status**: P0 COMPLETE, P1-P3 PLANNED

## What Exists

```
src/msb_v3/speech/
├── __init__.py          # package
├── models.py            # AudioBuffer, Transcript, SpeakerIdentity, VoiceCommand, PipelineResult
├── capture.py           # audio from file (WAV) + microphone (PyAudio)
├── transcribe.py        # STT (openai-whisper, mlx, faster-whisper)
├── speaker.py           # speaker verification (resemblyzer)
├── intent.py            # intent extraction (pattern matching)
├── pipeline.py          # full STT pipeline with auth gate
├── response.py          # voice response loop (listen → think → speak)
├── safety.py            # P0: Vesta policy gate, risk classification, confidence, audit
└── tts/
    └── engine.py        # TTS (macOS say, pyttsx3)
```

## What's Connected

| Module | Wired To | Status |
|--------|----------|--------|
| `capture.py` | File input, microphone | ✅ VERIFIED |
| `transcribe.py` | whisper (local) | ✅ VERIFIED |
| `speaker.py` | resemblyzer (local) | ✅ VERIFIED |
| `intent.py` | Pattern matching → endpoint + params | ✅ VERIFIED |
| `pipeline.py` | capture → transcribe → verify → authorize → intent | ✅ VERIFIED |
| `response.py` | pipeline + safety gate + TTS | ✅ VERIFIED |
| `safety.py` | VoicePolicyGate + confidence + confirmation | ✅ VERIFIED |
| `tts/engine.py` | macOS say + pyttsx3 | ✅ VERIFIED |
| API route | `/multimodal/speech/command` → VoiceResponder | ✅ VERIFIED |

## What's Verified (with evidence)

| Claim | Evidence |
|-------|----------|
| End-to-end voice loop works | 3 WAV files tested, live mic tested |
| Speaker verification works | resemblyzer with threshold 0.75 |
| Safety gate blocks HIGH/CRITICAL | API test: deploy → confirm, killswitch → confirm |
| Safety gate allows LOW/MEDIUM | API test: status → allowed |
| TTS speaks aloud | macOS say verified |
| 84 tests pass | pytest run |

## What's Missing (from the roadmap)

| Item | Priority | Status |
|------|----------|--------|
| VAD (voice activity detection) | P1 | NOT STARTED |
| Variable-length capture (not fixed 5s) | P1 | NOT STARTED |
| Whisper model benchmarking | P1 | NOT STARTED |
| Confidence thresholds tuning | P1 | NOT STARTED |
| Wake word detection | P2 | NOT STARTED |
| Continuous listening | P2 | NOT STARTED |
| Barge-in (interrupt TTS) | P2 | NOT STARTED |
| Conversational state | P2 | NOT STARTED |
| VoiceSession audit trail | P0 | ✅ IMPLEMENTED |
| Event bus for voice events | P3 | NOT STARTED |
| Fault injection tests | P3 | NOT STARTED |

## Dependency Graph

```
capture.py
    ↓
transcribe.py → whisper
    ↓
speaker.py → resemblyzer
    ↓
intent.py → pattern matching
    ↓
safety.py → VoicePolicyGate (P0)
    ↓
response.py → VoiceResponder
    ↓
tts/engine.py → macOS say
    ↓
API: /multimodal/speech/command
```

## Critical Path

The bottleneck is **whisper transcription** (~4-8s on CPU). Everything else is <0.1s.
P1 (VAD + variable capture) will reduce perceived latency by not waiting for fixed 5s.
