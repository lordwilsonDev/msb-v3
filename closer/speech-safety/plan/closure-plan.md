# Speech Safety — Closure Plan

**Date**: 2026-08-27
**Goal**: Voice subsystem production-ready (safe, fast, alive)

## Phase 1: P0 Safety (COMPLETE)

| Task | Status | Evidence |
|------|--------|----------|
| TASK-001: VoicePolicyGate | ✅ VERIFIED | 28 tests, safety.py |
| TASK-002: Risk classification | ✅ VERIFIED | LOW/MEDIUM/HIGH/CRITICAL |
| TASK-003: Confidence scoring | ✅ VERIFIED | Weighted average |
| TASK-004: Confirmation flow | ✅ VERIFIED | HIGH/CRITICAL require confirm |
| TASK-005: VoiceSession audit | ✅ VERIFIED | Full causal chain |
| TASK-006: Wire into VoiceResponder | ✅ VERIFIED | API live, 84 tests pass |

**Phase 1 closure: 100%**

## Phase 2: P1 Speed (NEXT)

| Task | Status | Depends On | Acceptance |
|------|--------|------------|------------|
| TASK-007: Add VAD (webrtcvad) | PLANNED | — | Silence detection works |
| TASK-008: Variable-length capture | PLANNED | TASK-007 | Capture stops on speech end |
| TASK-009: Whisper benchmark (tiny/base/small) | PLANNED | — | Table of latency vs accuracy |
| TASK-010: Tune confidence thresholds | PLANNED | TASK-009 | Thresholds documented |
| TASK-011: Add latency breakdown to VoiceSession | PLANNED | — | Per-stage timing in session |

**Phase 2 closure: 0%**

## Phase 3: P2 Alive (AFTER P1)

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

## Dependency DAG

```
TASK-007 (VAD)
    ↓
TASK-008 (Variable capture)
    ↓
TASK-013 (Continuous listening)
    ↓
TASK-014 (Barge-in)

TASK-009 (Whisper benchmark)
    ↓
TASK-010 (Tune thresholds)

TASK-011 (Latency breakdown)
    ↓
TASK-018 (Voice metrics)

TASK-012 (Wake word) ← needs TASK-007
TASK-015 (Conversational state) ← standalone
TASK-016 (Event bus) ← standalone
TASK-017 (Fault injection) ← needs all above
```

## Current Closure Score

```
Total tasks:     18
Completed:        6 (Phase 1)
Remaining:       12 (Phase 2-4)
Closure:         33%
```
