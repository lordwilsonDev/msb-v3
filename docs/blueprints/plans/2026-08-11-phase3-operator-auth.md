# Phase 3 — Operator Auth on the Control Surfaces

**Status:** implemented 2026-08-12
**Scope:** `/governance` + `/flywheel` control (state-changing) endpoints.
**Precedent:** the /v1 adapter's fail-closed bearer gate (`openai_compat.py`)
and audit `smi-017`'s prescription (FastAPI dependency on `settings.operator_token`,
fail-closed when unset).

## Why

The brakes (Phase 0B) and the loop (Phase 2) are reachable over HTTP with no
authentication — arm/disarm/approve/start-turns were loopback-trusted. That
was declared debt ("operator auth lands in Phase 3 hardening", CLAUDE.md).
Phase 3 closes it for the control surfaces: anyone who can reach the port can
no longer flip the kill switch or approve irreversible work without the
operator token.

## Design contract

- **One shared gate** — `msb_v3/api/auth.py::bearer_gate` (fail-closed 503
  when the credential is unset, 401 on mismatch, `secrets.compare_digest`
  constant-time bytes comparison, credential read live from settings so a
  config change applies without a restart). `require_operator` wraps it with
  `settings.operator_token` (MSB_OPERATOR_TOKEN).
- **Protected (state-changing writes):**
  - `/governance`: `POST /budget/reset`, `POST /killswitch/arm`,
    `POST /killswitch/disarm`, `POST /approvals`, `POST /approvals/{id}/approve`,
    `POST /approvals/{id}/reject`, `POST /approvals/{id}/cancel`,
    `POST /check` (it spends budget units + audits — not side-effect-free).
  - `/flywheel`: `POST /flywheel/turn`, `POST /flywheel/turns/{id}/approve`,
    `POST /flywheel/turns/{id}/resume`.
- **Open (reads only):** `/governance/status`, `/governance/budget`,
  `/governance/approvals` (GET), `/flywheel/turns` (GET),
  `/flywheel/turns/{id}` (GET) — the cockpit and status surfaces keep working
  unauthenticated.
- **Fail-closed when unset:** no MSB_OPERATOR_TOKEN ⇒ control surface is 503,
  exactly like the /v1 adapter without OPENAI_API_KEY. The operator sets the
  token in `.env` (`scripts/set-operator-token.sh` generates one idempotently).
- **CLIs unaffected:** `python -m msb_v3.governance` / `msb_v3.flywheel` are
  in-process operator consoles, not HTTP — no token needed there.
- **/v1 aligned:** `openai_compat._check_auth` now calls the same shared gate
  (message-preserving; no test asserts on those messages).

## Operator usage

```bash
# first time only — generates MSB_OPERATOR_TOKEN into .env (idempotent)
bash scripts/set-operator-token.sh
bash scripts/start.sh restart   # reloads .env

# control endpoints now require the bearer token
curl -X POST http://127.0.0.1:8766/governance/killswitch/arm \
  -H "Authorization: Bearer $MSB_OPERATOR_TOKEN" \
  -H 'Content-Type: application/json' -d '{"operator":"wilson","reason":"..."}'
```

Reads (status/cockpit/turn lists) need no token.

## Validation gates

- Gate 0: control POST with no token -> 503, and nothing mutates (no turn
  created, no approval decided).
- Gate 1: control POST with a wrong token -> 401.
- Gate 2: control POST with the correct token -> 200/201/202 as before.
- Gate 3: reads stay 200 with the token unset.
- Gate 4: full suite + portability green; live server verified over HTTP.
