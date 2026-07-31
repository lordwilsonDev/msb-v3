# Triumvirate API — Triumvirate Operating System Surfaces

Base path: `/triumvirate`

## Phase 1 — Meta-Cognitive Planner
- `POST /triumvirate/plan` — decompose goal into 5-stage plan payload
- `GET  /triumvirate/status` — current mission anchor state
- `POST /triumvirate/status/lock` — lock a new goal scope
- `GET  /triumvirate/status/verify` — scope hash verification
- `GET  /triumvirate/status/dashboard` — lightweight home-dashboard snapshot

## Phase 3 — Guardian Protocol
- `POST /triumvirate/guardian/scan` — static script risk scan
- `POST /triumvirate/guardian/sbom/register` — register server artifact
- `GET  /triumvirate/guardian/sbom/{server_id}` — trust check
- `POST /triumvirate/guardian/least-privilege` — token/scope enforcement
- `POST /triumvirate/guardian/poison-pill/arm` — arm kill switch
- `POST /triumvirate/guardian/poison-pill/detonate` — execute kill switch

## Phase 4 — Argus Auditor
- `POST /triumvirate/argus/audit` — run self-annealing audits
- `GET  /triumvirate/argus/mulch` — read mulch learnings store

## Phase 5 — Hardware Sovereignty
- `POST /triumvirate/cluster/peers` — register peer node
- `GET  /triumvirate/cluster/peers` — list cluster peers
- `POST /triumvirate/hippocampus/upsert` — store vector chunk
- `POST /triumvirate/hippocampus/search` — cosine search over embeddings

## Phase 6 — Multimodal Interfaces
- `POST /triumvirate/multimodal/vision/capture` — screen capture stub
- `POST /triumvirate/multimodal/haptic/heartbeat` — SAC haptic poll
- `POST /triumvirate/multimodal/speech/command` — transcript→endpoint mapper
