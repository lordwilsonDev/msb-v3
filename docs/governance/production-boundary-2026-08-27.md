# MSB-v3 Production Boundary

Date: 2026-08-27
Status: in-progress
Owner: project operator

## Operating rule

`main` is the release lane. Feature and subsystem experiments belong on branches and must not be promoted while mandatory CI is red. A red mandatory gate is classified, fixed, isolated, or explicitly gated before feature expansion resumes.

## Experimental exceptions

### Speech

- Subsystem: speech
- Status: EXPERIMENTAL
- Date: 2026-08-27
- Reason: P0→P2 voice pipeline development
- Scope: capture, VAD, wake word, continuous stream, barge-in, conversational state, and existing safety wiring
- Owner: project operator
- Exit criteria: speech acceptance gate covering authorization, Vesta, ActionGate, governed tool execution, verification, evidence, recovery, kill switch, and resource limits
- Promotion target: future release

### energy_matrix

- Subsystem: energy_matrix
- Status: EXPERIMENTAL
- Date: 2026-08-27
- Reason: telemetry and scheduling subsystem remains outside the canonical release contract
- Scope: telemetry, scheduler, and flywheel-health integration currently covered by its existing tests
- Owner: project operator
- Exit criteria: explicit production contract, failure-mode coverage, operational evidence, and release-gate inclusion
- Promotion target: future release

## Promotion invariant

Voice must not create a privileged execution path:

`VOICE → AUTHORIZATION → VESTA → ACTION GATE → GOVERNED TOOL → VERIFY → EVIDENCE`

This document does not claim production closure. It records the boundary and the work required to promote either subsystem.
