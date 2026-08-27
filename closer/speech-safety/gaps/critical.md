# Speech Safety — Critical Gaps

**Date**: 2026-08-27

## What's blocking production readiness

### GAP-001: Fixed 5-second recording (P1)
**Status**: OPEN
**Impact**: HIGH — forces user to wait 5 seconds even for short commands
**Root cause**: `capture_from_microphone()` uses fixed `duration_seconds=5.0`
**Fix**: Add VAD (voice activity detection) to detect speech start/end dynamically
**Evidence**: Live mic test shows 5s capture regardless of speech length
**Acceptance**: Capture stops within 0.5s of speech ending

### GAP-002: No confidence thresholds tuning (P1)
**Status**: OPEN
**Impact**: MEDIUM — hardcoded thresholds may be too aggressive or too lax
**Root cause**: `VoicePolicyGate` uses fixed thresholds (0.5, 0.75, 0.5, 0.6)
**Fix**: Benchmark whisper models + tune thresholds based on real data
**Evidence**: No benchmarking data exists yet
**Acceptance**: Thresholds documented with rationale, false positive/negative rates measured

### GAP-003: No whisper model benchmarking (P1)
**Status**: OPEN
**Impact**: MEDIUM — don't know which model gives best latency/accuracy tradeoff
**Root cause**: Only tested with whisper "small" model
**Fix**: Benchmark tiny/base/small/medium on real commands
**Evidence**: No benchmark data exists
**Acceptance**: Table of model vs latency vs accuracy vs RAM

### GAP-004: No conversational state (P2)
**Status**: OPEN
**Impact**: MEDIUM — system can't handle follow-up commands
**Root cause**: Each request is stateless
**Fix**: Add session state for multi-turn conversations
**Evidence**: Current system treats every input independently
**Acceptance**: "Research BitNet" → "What aspect?" → "Training efficiency" works

### GAP-005: No wake word (P2)
**Status**: OPEN
**Impact**: LOW — user must manually trigger each interaction
**Root cause**: Not implemented
**Fix**: Add wake word detection (e.g., "Hey Sovereign")
**Evidence**: Not implemented
**Acceptance**: System responds only after wake word

### GAP-006: No barge-in (P2)
**Status**: OPEN
**Impact**: LOW — user can't interrupt TTS while it's speaking
**Root cause**: TTS blocks until complete
**Fix**: Add VAD during TTS playback, stop on speech detection
**Evidence**: System speaks without interruption
**Acceptance**: User can say "stop" while system is speaking

### GAP-007: No fault injection tests (P3)
**Status**: OPEN
**Impact**: LOW — system hasn't been tested under adversarial conditions
**Root cause**: Only happy path tested
**Fix**: Add tests for whisper failure, mic failure, speaker rejection, etc.
**Evidence**: Only 84 tests, all happy path
**Acceptance**: Tests for every failure mode in the roadmap

## What's NOT a gap (already resolved)

| Item | Status | Evidence |
|------|--------|----------|
| Vesta policy gate | ✅ RESOLVED | `safety.py` + 28 tests |
| Risk classification | ✅ RESOLVED | LOW/MEDIUM/HIGH/CRITICAL |
| Confirmation flow | ✅ RESOLVED | HIGH/CRITICAL require "confirm" |
| Confidence scoring | ✅ RESOLVED | Weighted average |
| Audit trail | ✅ RESOLVED | VoiceSession dataclass |
| API wiring | ✅ RESOLVED | `/multimodal/speech/command` live |
| Safety gate integration | ✅ RESOLVED | VoiceResponder uses VoicePolicyGate |
