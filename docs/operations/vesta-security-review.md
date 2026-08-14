# Vesta/Node Trust Boundary — Security Review

**Date:** 2026-08-14 · **Reviewer:** Buffy (AI review) · **Scope:** the
implemented `msb_v3/node/` and `msb_v3/vesta/` trust boundary, per the gate
in `2026-08-13-vesta-node-full-build-plan.md` §7 ("security review before
expanding the named shell allowlist or adding browser/application agency").

> **Method:** every control below was verified by reading the actual
> enforcement source, not the plan docs. Files reviewed in full:
> `node/crypto.py`, `node/protocol.py`, `node/identity.py`,
> `node/filesystem.py`, `node/api.py` (mounting), `vesta/transport.py`,
> `vesta/policy.py`, `vesta/shell.py`, `vesta/runtime.py`,
> `vesta/evidence.py`, `vesta/api.py` (route wiring), `core/config.py`
> (defaults). Test suite state at review time: 866 passed / 3 skipped,
> mypy clean, ruff clean.

## 1. Verified controls

| # | Control | Where | Verdict |
|---|---|---|---|
| 1 | Pairing code compared constant-time; empty code = enrollment closed | `identity.enroll` (`hmac.compare_digest`) | ✓ |
| 2 | Enrollment accepts only uncompressed P-256 keys; revoked devices can't re-enroll; key swap rejected | `identity.enroll` | ✓ |
| 3 | Challenges are one-time (`used` flag) and expire (2× clock skew) | `identity.challenge` / `open_session` | ✓ |
| 4 | Clock skew enforced in both directions (60 s default); session TTL (900 s) with durable expiry | `identity.verify_request`, `open_session` | ✓ |
| 5 | Replay state is **durable** (SQLite `node_replays`, `UNIQUE(nonce)`, PK `request_id`) — race-safe, survives restart | `identity.verify_request` | ✓ |
| 6 | Signature verified over canonical JSON (sorted keys, compact separators), protocol-versioned | `protocol.canonical_json`, `crypto.verify` | ✓ |
| 7 | ECDSA uses the declared `cryptography` provider; low-S normalized on sign and range/low-S checked on verify | `node/crypto.py` | ✓ |
| 8 | Policy is deterministic; unknown capability → DENY; chat limited to exactly `model.inference` + `memory.read`; shell requires exactly `{shell.exec}` | `vesta/policy.py` | ✓ |
| 9 | Shell: named absolute executables only, no `shell=True`, scrubbed env, process-group SIGKILL on timeout, bounded args/output, `echo` flags and `pwd` args denied | `vesta/shell.py` | ✓ |
| 10 | Approval revalidation: command hash re-checked, `authorize_shell` re-run, kill switch checked before execution | `shell.approve_and_execute` | ✓ |
| 11 | Signed owner ACK binds exact contract: route approval ID + command SHA-256 + policy version (shell); approval ID + target path + payload/precondition hashes + policy version (write). Server compares against the durable approval — client can't alter scope | `vesta/api.py` `shell_signed_approve` / `signed_write_approve` | ✓ |
| 12 | Filesystem: `resolve` + `relative_to(root)`; symlink writes rejected; atomic `os.replace` after fsync; precondition and postcondition hashes; rollback verified | `node/filesystem.py` | ✓ |
| 13 | Transport admission uses `request.client.host` (direct peer address), never `X-Forwarded-For`; fail-closed when required; invalid CIDR config raises | `vesta/transport.py` | ✓ |
| 14 | Transport gate applied to **every** mutation/approval route (`/vesta/chat`, `/signed-chat`, `/read`, `/signed-read`, `/execute`, `/shell/execute`, all approve/reject paths, `/authorize`) | `vesta/api.py` | ✓ |
| 15 | Task lifecycle: serialized transitions (`BEGIN IMMEDIATE`), explicit transition matrix, terminal states locked; `recover_incomplete()` quarantines `EXECUTING`/`VERIFYING`/`RECOVERING` after restart | `vesta/runtime.py` | ✓ |
| 16 | Evidence: content-addressed (SHA-256), atomic fsync'd writes, chmod 600; lookup re-verifies the hash and reports corruption rather than silently repairing | `vesta/evidence.py` | ✓ |
| 17 | Fail-closed defaults: `MSB_VESTA_REQUIRE_TUNNEL=0` default, allowed CIDRs loopback-only by default, sandbox root inside the repo, replay state on disk | `core/config.py`, `.env.example` | ✓ |

## 2. Findings

### F1 — Replay/challenge tables grow unbounded (low, operational)
`node_replays` and `node_challenges` have no retention policy. Records only
need to outlive the session window + skew; after session expiry they can be
pruned. Not a security gap (replay protection is exactly as strong as the
retention window), but a long-running node accumulates rows forever.
**Recommendation:** a startup/prune job that deletes replay rows whose
session has expired (join on `node_sessions.expires_at`) and challenges
older than skew × 2.

> **Resolved 2026-08-14:** `IdentityStore.prune()` deletes replay rows whose
> session is no longer ACTIVE or has expired, consumed/stale challenges
> (older than skew × 2 or from non-active devices), and expired/revoked
> sessions — called at the start of every challenge/session/request path so
> the tables stay bounded with no background job. Tests:
> `tests/node/test_replay_pruning.py` (stale-row removal + prune-before-
> validation on the request path).

### F2 — Read-only /vesta GETs are not transport-gated (low, defense-in-depth)
`/vesta/status`, `/manifest`, `/capabilities`, `/routes`, `/ledger/verify`
are unauthenticated and reachable from any interface even when
`MSB_VESTA_REQUIRE_TUNNEL=1`. Content is informational only, but
`/vesta/msb-health` also discloses `ollama_url` and the model name. With
tunnel mode on, the whole router should require transport (loopback is in
the default allowed CIDRs, so local operations keep working).
**Recommendation:** when `transport_required`, apply
`require_vesta_transport` at the router level; optionally make
`/vesta/msb-health` operator-gated.

> **Resolved 2026-08-14:** `require_vesta_transport` is now a router-level
> dependency on **both** the `/vesta` and `/node/v1` routers, so when
> `MSB_VESTA_REQUIRE_TUNNEL=1` the read-only views and the raw signed-
> executor surface are reachable only from allowed peer CIDRs (loopback is
> in the default set, so local ops keep working). `/vesta/msb-health` is
> intentionally left client-reachable for the phone status view — it is
> tunnel-gated like everything else. Tests:
> `tests/vesta/test_vesta.py::test_read_only_vesta_views_require_tunnel_when_enabled`,
> `tests/node/test_node_api.py::test_node_surface_requires_tunnel_when_enabled`.

### F3 — Shell approval status not reconciled on execution failure (low, audit hygiene)
If `approve_and_execute` fails after `approvals.approve()` (e.g. kill
switch, verification failure), the task goes `QUARANTINED` but the approval
row stays `APPROVED`. Re-approval is impossible (decided rows are locked),
so this is not a bypass — but an operator reading the approvals table sees
"APPROVED" next to a quarantined task. **Recommendation:** on quarantine,
mark the approval `VOID`/`REJECTED` with the failure reason.

> **Resolved 2026-08-14:** both approval stores (`VestaShellApprovalStore`,
> `VestaApprovalStore`) gained a terminal `void()` (APPROVED → VOID); both
> services call it on the kill-switch path and on the failure/quarantine
> path, recording an `approval.voided` audit event. VOID is terminal —
> `approve`/`reject` refuse it — so a voided approval can never be
> re-decided into an execution. Tests: `tests/vesta/test_shell.py` +
> `tests/vesta/test_write.py` (quarantine paths assert VOID + voided event;
> terminal-state semantics).

### F4 — WireGuard deployment is the actual remaining gate (expected)
WireGuard is **not installed** on this machine (`wg`/`wg-quick` absent, no
app bundle, no configs). The utun interfaces present belong to other tunnel
services. Until the tunnel exists and interface-bound admission is proven,
`MSB_VESTA_REQUIRE_TUNNEL` must stay `0` — which the defaults already
guarantee. See `2026-08-14-wireguard-preflight-adr.md` for the gathered
facts and the operator steps.

## 3. Verdict

**The implemented boundary is sound and fails closed.** The inversion the
plans demand — authenticated human intent becomes a constrained, verified,
auditable action; the model never grants itself authority — is enforced in
code, not just documented. The 17 controls above hold under adversarial
review of the enforcement paths (replay, scope escape, approval replay,
signature forgery, evidence tamper, restart recovery).

**Gate decision: findings F1–F3 are now addressed (2026-08-14) with tests.
Expanding the shell allowlist or adding browser/application agency remains
BLOCKED until:**
1. the WireGuard private overlay is deployed and public-interface denial is
   proven (F4 / the preflight ADR `2026-08-14-wireguard-preflight-adr.md`),
   and
2. each new capability lands with the full policy → scope → contract →
   budget → verification → rollback → audit → adversarial-test matrix from
   the build plan §6.

No allowlist expansion or new actuator authority is recommended from this
review.
