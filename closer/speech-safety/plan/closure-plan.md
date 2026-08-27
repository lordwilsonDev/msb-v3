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

## Phase 2: P1 Speed (IN PROGRESS)

| Task | Status | Evidence |
|------|--------|----------|
| TASK-007: Add VAD (webrtcvad) | ✅ VERIFIED | 11 tests, vad.py |
| TASK-008: Variable-length capture | ✅ VERIFIED | capture_intelligent() |
| TASK-009: Whisper benchmark | ✅ VERIFIED | tiny=1.0s, base=1.4s, small=1.8s |
| TASK-010: Tune confidence thresholds | ✅ VERIFIED | Defaults documented |
| TASK-011: Latency breakdown in VoiceSession | PLANNED | — |

**Phase 2 closure: 80%**

## Phase 3: P2 Alive (AFTER P2)

| Task | Status | Depends On | Acceptance |
|------|--------|------------|------------|
| TASK-012: Wake word detection | PLANNED | TASK-007 | Responds after wake word |
| TASK-013: Continuous listening loop | PLANNED | TASK-008 | Always-on microphone |
| TASK-014: Barge-in (interrupt TTS) | PLANNED | TASK-008 | User can say "stop" |
| TASK-015: Conversational state | PLANNED | — | Multi-turn dialogue works |

**Phase 3 closure: 0%**

## Phase 4: P3 Subsystem (AFTER P2)

| Task | Status | Depends On | Acceptance |
|------|--------|------------|------------|
| TASK-016: Event bus for voice events | PLANNED | — | Every action emits event |
| TASK-017: Fault injection tests | PLANNED | — | Tests for every failure mode |
| TASK-018: Voice metrics (Prometheus) | PLANNED | — | Latency, success rate tracked |

**Phase 4 closure: 0%**

## Current Closure Score

```
Total tasks:     18
Completed:       9 (Phase 1 + Phase 2 partial)
Remaining:        9 (Phase 2 tail + Phase 3-4)
Closure:         50%
```
