# MSB v3

Sovereign local-first AI runtime: FastAPI + SQLite + Qwen3/Ollama + Prometheus.

## Endpoints

- `/chat` — POST `{"query": "...", "session": "default", "system": "...", "tools": [...]}`
  Tools are executed bounded (`max_steps=4`) and the response includes `history_count`.
- `/memory/{session}` — GET recent, POST append, DELETE clear
- `/metrics/` — JSON metrics summary
- `/metrics/prometheus` — Prometheus scrape
- `/system/health` — deep health check
- `/system/config` — runtime config, secrets masked
- `/system/routes` — live route registry
- `/status` — service/version/model/ready
- `/health` — liveness
- `/ready` — readiness
- `/dashboard` — unified studio HTML
- `/docs` — Swagger UI
- `/node/v1/status` — Sovereign Node status
- `/node/v1/auth/*` — device enrollment and signed sessions
- `/node/v1/engage` — policy-governed signed intent execution (first slice: scoped FILE_READ)
- `/vesta/status` and `/vesta/msb-health` — Vesta trust-boundary observation
- `/vesta/manifest`, `/vesta/capabilities`, `/vesta/routes` — Phase 0 discovery views
- `/vesta/chat` — operator-authenticated A-BIND wrapper around native `/chat`
- `/vesta/signed-chat` — device-session-authenticated chat for the iPhone protocol
- `/vesta/read` — operator-authenticated, sandbox-scoped FILE_READ with evidence
- `/vesta/signed-read` — signed-device sandbox FILE_READ with server-owned capability policy
- `/vesta/ledger/verify` — shared MSB hash-chain verification
- `/vesta/tasks/{task_id}` — durable task state and transition history
- `/vesta/evidence/{evidence_id}` — operator-authenticated evidence metadata/hash verification
- `/vesta/execute` — submit an exact FILE_WRITE request for owner approval
- `/vesta/approvals/{approval_id}/approve|reject` — maintenance-token decision for an exact FILE_WRITE contract
- `/vesta/approvals/{approval_id}/signed-approve` — cryptographic enrolled-device owner ACK for the exact FILE_WRITE contract
- `/vesta/shell/execute` — submit an allowlisted SHELL_EXEC contract for approval
- `/vesta/shell/approvals/{approval_id}/approve|reject` — maintenance-token decision for an exact shell contract
- `/vesta/shell/approvals/{approval_id}/signed-approve` — cryptographic enrolled-device owner ACK for the exact contract

SHELL_EXEC is currently approval-only and deliberately tiny: only named `echo`
and `pwd` commands are available, with no shell interpolation, no arbitrary
executable paths, no network capability, a sandbox working directory, bounded
arguments/output, and a hard timeout. A command is not considered complete until
its return code, timeout state, output bound, and optional expected stdout are
verified. The phone approval path signs the approval ID, exact command SHA-256,
and policy version; replayed, expired, or mismatched acknowledgements fail
closed. The maintenance bearer-token approval remains available for local
operations and is not the phone identity.

The FILE_WRITE phone approval path signs the approval ID, exact target path,
payload SHA-256, precondition SHA-256, and policy version. Vesta compares those
fields with the durable approval before the atomic write; wrong-target, replayed,
expired, or tampered acknowledgements fail closed.

The read-only filesystem gate is evidence-backed and bounded to the configured
`MSB_NODE_SANDBOX_ROOT`. It records the request, policy decision, content hash,
verification receipt, task lifecycle, and audit references. Path traversal,
symlink escapes, missing files, size limits, kill state, and verification
failures fail closed; it does not grant shell, network, or host filesystem access.

For hardware-independent development, `make vesta-loopback` runs an ephemeral
signed device through the real enrollment/session/signed-chat path over
loopback. See [docs/operations/vesta-loopback.md](docs/operations/vesta-loopback.md).

## Vesta integration

Vesta is the MSB trust/evidence perimeter, not a second agent runtime. Phase 0–2
allows only `model.inference` and `memory.read`, wraps the existing chat harness
with an immutable A-BIND, and records request/authorization/MSB/response events
through the existing `msb_v3.uac.AuditChain`. The controlled `/vesta/chat`
surface requires `Authorization: Bearer $MSB_OPERATOR_TOKEN`; native `/chat`
remains available for local development. Optional tunnel-only admission is
controlled by `MSB_VESTA_REQUIRE_TUNNEL` and `MSB_VESTA_ALLOWED_CIDRS` after
WireGuard is deployed. Sensors, privileged tools, external transmission, and
physical agency are deliberately deferred.

The implementation contract is documented in
[docs/blueprints/plans/2026-08-13-vesta-msb-integration-spec.md](docs/blueprints/plans/2026-08-13-vesta-msb-integration-spec.md).

## Env

See `.env.example`. Key vars:

- `MSB_HOST`, `MSB_PORT`
- `OLLAMA_URL`, `OLLAMA_MODEL`
- `MSB_DB_PATH`

## Open WebUI (ready-made chat UI)

MSB exposes an OpenAI-compatible `/v1` adapter so Open WebUI (or any OpenAI
SDK client) can drive the native harness. Setup, auth, and Tencent COS file
storage: [docs/open-webui-adapter-v1.md](docs/open-webui-adapter-v1.md).

## Run

bash scripts/start.sh

## Test

bash scripts/test.sh

Pre-push gate — install once per clone (git hooks aren't versioned):

```
make hooks-install
```

This installs a pre-push hook that runs `make portability` (full suite from
a foreign checkout path) before every push and blocks the push on failure.
Bypass explicitly with `MSB_SKIP_PORTABILITY=1 git push`; remove with
`make hooks-uninstall`.
