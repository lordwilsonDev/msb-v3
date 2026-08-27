# Speech Safety — Closure Plan

**Date**: 2026-08-27
**Goal**: Voice subsystem production-ready (safe, fast, alive)

## Phase 1: P0 Safety (COMPLETE ✅)

| Task | Status | Evidence |
|------|--------|----------|
| TASK-001: VoicePolicyGate | ✅ VERIFIED | 28 tests, safety.py |
| TASK-002: Risk classification | ✅ VERIFIED | LOW/MEDIUM/HIGH/CRITICAL |
| TASK-003: Confidence scoring | ✅ VERIFIED | Weighted average |
| TASK-004: Confirmation flow | ✅ VERIFIED | HIGH/CRITICAL require confirm |
| TASK-005: VoiceSession audit | ✅ VERIFIED | Full causal chain |
| TASK-006: Wire into VoiceResponder | ✅ VERIFIED | API live, 84 tests pass |

**Phase 1 closure: 100% ✅**

## Phase 2: P1 Speed (COMPLETE ✅)

| Task | Status | Evidence |
|------|--------|----------|
| TASK-007: Add VAD (webrtcvad) | ✅ VERIFIED | 11 tests, vad.py |
| TASK-008: Variable-length capture | ✅ VERIFIED | capture_intelligent() |
| TASK-009: Whisper benchmark | ✅ VERIFIED | tiny=1.0s, base=1.4s, small=1.8s |
| TASK-010: Tune confidence thresholds | ✅ VERIFIED | Defaults documented |
| TASK-011: Latency breakdown in VoiceSession | ✅ VERIFIED | respond_with_session() + 8 tests |

**Phase 2 closure: 100% ✅**

## Phase 3: P2 Alive (COMPLETE ✅)

| Task | Status | Evidence |
|------|--------|----------|
| TASK-012: Wake word detection | ✅ VERIFIED | 19 tests, wakeword.py |
| TASK-013: Continuous listening loop | ✅ VERIFIED | 11 tests, stream.py |
| TASK-014: Barge-in (interrupt TTS) | ✅ VERIFIED | 9 tests, bargein.py |
| TASK-015: Conversational state | ✅ VERIFIED | 17 tests, conversation.py |

**Phase 3 closure: 100% ✅**

## Phase 4: P3 Subsystem (NEXT)

| Task | Status | Depends On | Acceptance |
|------|--------|------------|------------|
| TASK-016: Event bus for voice events | PLANNED | — | Every action emits event |
| TASK-017: Fault injection tests | PLANNED | — | Tests for every failure mode |
| TASK-018: Voice metrics (Prometheus) | PLANNED | — | Latency, success rate tracked |

**Phase 4 closure: 0%**

## Current Closure Score

```
Total tasks:     18
Completed:      15 (Phase 1-3)
Remaining:       3 (Phase 4)
Closure:         83%
```
