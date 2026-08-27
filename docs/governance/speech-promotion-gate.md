# Speech Promotion Gate

Date: 2026-08-27
Status: experimental

Speech remains outside the release lane until every item below has evidence from deterministic tests or a declared live/soak gate.

- [ ] microphone
- [ ] VAD
- [ ] wake word
- [ ] transcription
- [ ] speaker verification
- [ ] Vesta safety gate
- [ ] ActionGate
- [ ] intent extraction
- [ ] multi-turn state
- [ ] barge-in
- [ ] TTS
- [ ] audit events
- [ ] failure recovery
- [ ] kill switch
- [ ] resource limits
- [ ] graceful shutdown
- [ ] no governance bypass
- [ ] 30-minute soak
- [ ] 2-hour soak
- [ ] 8-hour soak
- [ ] 24-hour soak

## Required execution chain

`VOICE → AUTHORIZATION → VESTA → ACTION GATE → GOVERNED TOOL → VERIFY → EVIDENCE`

A local demonstration or passing unit suite is insufficient for promotion. The gate must also record environment, dependency availability, failure classification, and reproducible evidence.
