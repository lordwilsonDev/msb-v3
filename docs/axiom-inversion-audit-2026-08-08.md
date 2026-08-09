# AIL Audit — msb-v3 Deep Pass, Inverted

**Date:** 2026-08-08
**Method:** Axiom Inversion Logic — extract the deep pass's assumptions, invert each,
run evidence on BOTH sides, record enabling conditions. This document is the
inversion map; the patches it produced are listed at the bottom.

---

## Assumption 1 — "The security posture is the standout: MCP secret auth fails closed."

**Inversion:** The auth is real but *not universal* — it's per-endpoint, not per-system.
The headline claim over-generalized a verified property of `/mcp/proxy`.

**Supporting evidence (live-tested 2026-08-08):**
- `POST /mcp/proxy` with correct secret → `{"ok":true,"tool":"metrics_json",...}`
- `POST /mcp/proxy` with wrong secret → `{"detail":"unauthorized"}` (HTTP 401)
- `POST /mcp/proxy` with no secret → `{"detail":"unauthorized"}` (HTTP 401)
- Source confirms: `mcp_bridge.py:51-53` `_check_auth` raises 401 unless header == env secret.
- Path-traversal guard verified: `_normalize_vault_path` resolves and rejects outside-vault paths.

**Contradicting evidence:**
- `GET /mcp/tools` returns the **full tool manifest with NO secret** (live-tested). An
  unauthenticated caller learns every tool name, args, and description. Metadata leak,
  not a data leak — but "fails closed" is false for this endpoint.
- The bridge secret is **committed in plaintext** in `scripts/run.sh` (pre-existing).
- The same secret was **propagated into two NEW files during this session**:
  `sovereign-outcome-engine/outcome_engine.py` and
  `.agents/skills/blackswanlabz-sovereign-engagement/scripts/run_engagement.py`.
  A code review flagged the hardcoded default; it was acknowledged and NOT fixed.
- A second secret — `MSB_RAG_API_KEY` — is also hardcoded in `scripts/run.sh`.
- The bridge surface is **read-WRITE, not read-only**: `vault_write`, `vault_append`,
  `vault_patch`, `vault_delete`, `vault_move` all exist behind the single shared
  secret. A leaked secret = full vault write access. (The outcome engine only uses
  read tools — good — but the surface it sits on is not read-only.)

**Enabling conditions:** The claim holds ONLY if "the system" = "the /mcp/proxy path"
and the box is local-only (`MSB_HOST=127.0.0.1`).

**Plausibility of the original claim:** ~60%. Auth works, but the posture is
"one shared secret, several open/mutable seams" — fine for local, wrong framing
for a HaaSS product that will eventually face a network.

---

## Assumption 2 — "243/243 tests green = the whole codebase is genuinely healthy."

**Inversion:** Green at the unit layer does not prove the system layer. The tests
never exercise the server's actual surface.

**Supporting evidence:**
- 243 tests pass in ~4.3s (measured).
- 6 test files use `TestClient`; `tests/api/test_mcp_security.py` exists and covers
  the auth gate.

**Contradicting evidence:**
- **Zero test files call `create_app`** (grep: empty). The FastAPI app factory is never
  instantiated in a test.
- **Zero test files reference ollama** (grep: empty). The entire LLM path is untested.
- Of the 243 tests, the app-surface ones (TestClient) are a small fraction; the bulk
  are unit tests of `sovereign_runtime` / `personal_intelligence` modules that the
  live server does not even import.

**Enabling conditions:** "Healthy" is true only for module-level logic. End-to-end
health is currently evidenced by *live smoke calls* (this session's bridge usage),
not by the suite.

**Plausibility:** ~30%. The headline was overreach. Unit-green ≠ system-proven.

---

## Assumption 3 — "Multi-tenancy is a real moat."

**Inversion:** The moat is *drawn on the map but the water has never been tested*.

**Supporting evidence:**
- `tenants.py` + `tenant_chat.py` exist; `_tenant_path` uses `.resolve()` with a
  traversal guard; tenant files exist on disk (`data/tenants/*.json`).

**Contradicting evidence:**
- **Zero tenant-isolation tests exist** (grep for tenant in test dirs: empty).
  Nothing verifies that tenant A cannot read tenant B's file, memory, or chat.
- No per-tenant auth; a single shared bridge secret gates everything.

**Enabling conditions:** The moat becomes real the day a `test_tenant_isolation.py`
exists and passes. Until then it is a feature, not a verified property.

**Plausibility:** ~20% as claimed. Routing exists; isolation is unproven.

---

## Assumption 4 — "The 91 dirty files were a phantom: 79 are tracked runtime data."

**Inversion:** The data is NOT junk — it is the live system's runtime state, and it is
*correctly* churning. The problem is that git is tracking it, not that it's noise.

**Supporting evidence:**
- `git ls-files data/` → 82 tracked files (`msb_v3.db`, `memory_graph/*.json`,
  `tenants/*.json`).
- `.gitignore` exists and excludes `data/` — but files committed before the rule
  stay tracked (git never untracks retroactively).

**Contradicting evidence:**
- 10+ source modules write to `data/` paths (triumvirate/*, `core/config.py`,
  `observability/metrics.py`, `uac/stage_0_*`, `uac/audit_chain.py`, `api/home.py`).
  This is *the live runtime state of the canonical server* — deleting it would break
  the running system.
- Calling it a "phantom" understates it: untrack ≠ delete, and the untrack command
  must NOT be followed by deletion.

**Enabling conditions:** "Phantom" is true only if "churn" = "noise". It is churn
of real state. The fix (untrack) is still correct; the framing was wrong.

**Plausibility:** ~40%. Right conclusion, wrong reason.

---

## Assumption 5 — "sovereign_runtime is your unplugged moat — dock behind a flag."

**Inversion:** It is not a moat; it is a *greenfield satellite* that has never run
inside the live product loop. "Moat" inflates it.

**Supporting evidence:**
- Zero imports of `sovereign_runtime` from `msb_v3` (grep: empty).
- It passes its own tests in isolation (~21 test functions).

**Contradicting evidence:**
- Zero integration tests against the live app; the "brain" has never been exercised
  through the real server. Green-in-a-vacuum is the same overreach as Assumption 2.

**Enabling conditions:** It becomes a moat only after it is docked (behind a flag)
and verified through the live HTTP surface.

**Plausibility:** ~50%. "Promising satellite" is honest; "moat" is premature.

---

## Assumption 6 — "The supervisor-loop bug was real and is now fixed."

**Inversion:** This one HOLDS. `scripts/run.sh` now has a real restart loop:
`while true; do python -m msb_v3; sleep 2; done` with `set +e` around the child —
verified in source. The KeepAlive claim is true again.

**Plausibility:** ~95%. Not inverted.

---

## Things the deep pass missed entirely

1. **I propagated the bridge secret into 2 new files** (outcome_engine.py,
   run_engagement.py) — the worst miss, because a reviewer flagged it and it was
   acknowledged but not fixed. Patched below.
2. **`/mcp/tools` is unauthenticated** — metadata leak. Patched below.
3. **`MSB_RAG_API_KEY` hardcoded in run.sh** — second committed secret. Flagged;
   run.sh is launch-critical, so change requires sign-off.
4. **No tenant-isolation tests** behind the "moat" claim.
5. **The LLM path and the app factory have zero test coverage** — the "243 green"
   headline was unit-level only.
6. **Vault rglob tools** (`search_query`, `search_simple`, `tag_list`) read the whole
   vault with no depth/size cap — a cost/DoS consideration on a growing vault.
7. **`verify_build` writes to the vault** (`40_Memory/Verified-Builds-Log.md`) — a
   write side-effect inside a "verification" tool. By design, but worth knowing.

---

## Patches applied (this session)

| # | File | Change |
|---|------|--------|
| 1 | `sovereign-outcome-engine/outcome_engine.py` | Removed hardcoded bridge secret; `--vault` mode fails loudly (exit 2) when no secret is provided via `--secret` or `MCP_BRIDGE_SECRET` env. |
| 2 | `.agents/skills/blackswanlabz-sovereign-engagement/scripts/run_engagement.py` | Same: no hardcoded default; fails fast when secret missing. |
| 3 | `msb-v3/src/msb_v3/api/mcp_bridge.py` | `GET /mcp/tools` now requires the same `x-mcp-secret` gate as `/mcp/proxy`. The stdio `mcp_adapter.py` already sends the header, so nothing breaks. |

## Recommended next steps (need sign-off — effectful)

1. **Rotate the bridge secret** (it is now in git history in multiple repos) and move
   it to a gitignored `.env`; make `run.sh` source it. One command each, but it
   changes runtime config — your call.
2. **`git rm -r --cached data/`** in msb-v3 to stop tracking runtime state (keeps
   files on disk; verify the server still boots after).
3. **Write `test_tenant_isolation.py`** — tenant A must not read tenant B's data.
   This converts Assumption 3 from a claim into a verified property.
4. **Add one integration test** that boots `create_app` and exercises `/mcp/proxy`
   auth + one vault read — converts Assumption 2's counter-evidence into coverage.
5. **Restart msb-v3** so patch #3 (the `/mcp/tools` gate) takes effect.
