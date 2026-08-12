# Phase 0B — The Brakes: Implementation Plan

**Blueprint:** `docs/blueprints/2026-08-11-adaptive-build-environment.md` (§0.6 + Phase 0)
**Decisions (owner, 2026-08-11):** build directly on `main` · `setup.sh` only (no Dockerfile) · approval queue ships API + CLI (Cockpit UI is Phase 1)
**Gate:** `make test` + `make portability` green, ruff clean.

## Why this phase exists

The flywheel (§0.5) is Yang — generative, self-turning. It only gets to run itself once the Yin gates are load-bearing. These four brakes are the load-bearing gates, and they must be *provable*, not decorative:

1. **Ouroboros governor** — deterministic/subtractive throttle on MoIE expansion; convergence enforced.
2. **Budget + rate caps** — hard caps on research calls, tokens, loop iterations per period; the loop halts when a cap is hit (fail-closed).
3. **Approval queue** — nothing irreversible (stage 7 build, stage 8 combine, stage 9 promote, git commit, vault write) executes without explicit owner approval; queue survives restarts.
4. **Kill switch + audit** — one control to pause the whole loop; every autonomous action (and every brake refusal) written to the UAC audit chain so the loop is never a black box.

Plus the Phase 0 carryovers from the Phase 0A plan: **model provisioning** (`qwen3:8b` + `nomic-embed-text`) and a **`setup.sh`** reproducible rebuild path from `MANIFEST.md`.

## Global Constraints

- Fail-closed everywhere: unreadable state ⇒ treat as *halted/denied*, never *allowed*.
- SQLite for all persisted brake state (matches `AuditChain`/`axiom_library` house pattern; survives restarts).
- Reuse `msb_v3.uac.audit_chain.AuditChain` for audit writes — never invent a second audit path.
- Every module takes an injectable `db_path` (tests use `tmp_path`), defaulting to a derived path — house pattern from `AuditChain`.
- No new grand-named subsystems; no pydantic-settings; env-var-first config in `Settings`.
- All code ruff-clean (E9,F,I and full check), including tests (the Phase 0A lesson).

## Task 1: `msb_v3.governance` package core

New package `src/msb_v3/governance/` with five modules, one job each:

### `db.py`
`default_db_path() -> Path` — `Path(settings.db_path).parent / "governance" / "governance.db"` (same root the audit chain uses, sibling dir). All modules default to it.

### `budget.py` — `BudgetLedger`
Persistent per-category counters over a rolling window.
- Categories: `research_calls`, `tokens`, `iterations`.
- Table: `budget_entries(category TEXT, period_start REAL, spent INTEGER, PRIMARY KEY(category, period_start))`; a window older than `window_s` resets lazily on access.
- `spend(category, amount=1) -> bool` — thread-safe (Lock + single sqlite conn per call). Cap semantics, fail-closed: `-1` = unlimited (explicit opt-in), `0` = deny everything, `>0` = cap.
- `reset(category=None)`, `state() -> dict` (per category: spent, limit, remaining, window_s, period_start).

### `governor.py` — `OuroborosGovernor`
Deterministic subtractive throttle over the loop's own signals.
- Table: `governor_runs(seq, iteration, proposal_id, novelty REAL, duplicate_ratio REAL, created_at)` — bounded history (default 20).
- `advise(proposal_id, novelty, duplicate_ratio=0.0) -> GovernorVerdict`:
  - record the signal;
  - HALT on `stall_limit` (default 6) consecutive iterations with `novelty < novelty_min` (0.05) — converged or runaway;
  - HALT on `duplicate_ratio >= dup_ratio_halt` (0.5);
  - SLOW on declining novelty trend (recent-3 mean < prior-3 mean);
  - else CONTINUE.
- `GovernorVerdict`: `action` (CONTINUE/SLOW/HALT), `reason`, `metrics` (stall_count, dup_ratio, trend), `trim_candidates` (proposal ids in history above the dup threshold — subtractive suggestions the caller may park; v1 does not delete anything).
- Fail-closed: DB error ⇒ HALT.

### `approval.py` — `ApprovalQueue`
Restart-surviving queue for irreversible actions.
- Kinds (from blueprint §0.6): `build`, `combine`, `promote_knowledge`, `git_commit`, `vault_write`. Unknown kind on submit ⇒ ValueError.
- Table: `approval_items(id TEXT PK, kind, title, payload TEXT, evidence_refs TEXT, status TEXT, created_at, decided_at, decided_by, reason)`.
- `submit(kind, title, payload, evidence_refs=None) -> ApprovalItem` (PENDING; audit `approval.submitted`).
- `approve(id, operator, reason=None)` / `reject(id, operator, reason)` / `cancel(id, operator)` — transition only from PENDING (else `IdempotencyError`); each decision audited (`approval.approved/rejected/cancelled`).
- `get(id)`, `pending()`, `list(status=None)`.
- Item carries `evidence_refs` (e.g. UIM path) so the owner decides fast — CLI prints them now, Cockpit shows them in Phase 1.

### `killswitch.py` — `KillSwitch`
- Table: `kill_switch_state(id INTEGER CHECK(id=1), armed INTEGER, armed_at, armed_by, reason)`.
- `arm(operator, reason)`, `disarm(operator)`, `state()`, `is_armed() -> bool` (DB read failure ⇒ True).
- arm/disarm audited (`killswitch.armed` / `killswitch.disarmed`).

### `guard.py` — the flywheel's single enforcement point
- `Guard(killswitch, ledger, queue, governor)` and `check_run(action, kind=None, budget_units=None, approval_id=None, signal=None) -> GuardVerdict`:
  1. kill switch armed ⇒ HALT (`fail_closed`).
  2. budget category for the action exhausted ⇒ HALT (`budget_exhausted`).
  3. `kind in REQUIRES_APPROVAL`:
     - no `approval_id` ⇒ APPROVAL_REQUIRED (never execute);
     - `approval_id` present ⇒ verify item: APPROVED ⇒ OK, PENDING ⇒ APPROVAL_PENDING, anything else ⇒ APPROVAL_REQUIRED.
  4. governor signal provided ⇒ run governor; HALT/SLOW surface through.
- Every refusal (HALT/APPROVAL_REQUIRED/APPROVAL_PENDING) is audited (`governance.blocked`).
- `record_action(component, event_type, payload)` — thin wrapper over `AuditChain.append` for the loop to log executed actions (Phase 2 wires the loop; the audit surface exists now).

## Task 2: HTTP surface

`api.py` — `governance_router` (module-level singletons so tests monkeypatch them, matching `mcp_bridge`):
- `GET /governance/status` — kill switch + budgets + governor summary + pending approval count, one snapshot.
- `GET /governance/budget`, `POST /governance/budget/reset` (optional `category`).
- `POST /governance/killswitch/arm` (`reason`, `operator`) / `POST /governance/killswitch/disarm` (`operator`).
- `GET /governance/approvals?status=` / `POST /governance/approvals` (kind, title, payload, evidence_refs) / `POST /governance/approvals/{id}/approve|reject|cancel` (operator, reason).

Mounted in `app.py`: `app.include_router(governance_router, prefix="/governance", tags=["governance"])`.

## Task 3: CLI + Makefile

`cli.py` — `python -m msb_v3.governance status|arm|disarm|approvals|approve <id>|reject <id> <reason>|budget` (argparse subparsers, `--operator` default `operator`), human-readable output mirroring `msb_v3.ops` style.

Makefile targets: `governance-status`, `governance-arm` (REASON), `governance-disarm`, `governance-approvals`, `governance-approve` (ID), `governance-reject` (ID+REASON), `provision-models`, `setup`.

## Task 4: Provisioning + reproducible rebuild

- `scripts/provision-models.sh` — idempotent `ollama pull qwen3:8b` + `ollama pull nomic-embed-text`; skips models already present (checks `ollama list`), fails loudly if ollama is missing/unreachable.
- `scripts/setup.sh` — idempotent host rebuild from a fresh clone: verify `MANIFEST.md`, ensure the configured python, `pip install -e .`, install + bootstrap the three launchd agents (msb-v3, qdrant, backup), start qdrant, provision models, smoke `/health` on `:8766`. `set -euo pipefail`, echo each step, no destructive ops on existing state.

## Task 5: Config + docs

- `Settings`: `gov_budget_research_calls` (50), `gov_budget_tokens` (200000), `gov_budget_iterations` (100), `gov_budget_window_min` (1440), `gov_governor_stall_limit` (6), `gov_governor_novelty_min` (0.05), `gov_governor_dup_ratio_halt` (0.5), `gov_governor_history` (20) — env-overridable.
- CLAUDE.md: governance section (endpoints, CLI, brake semantics) + scripts list entries.

## Task 6: Tests (`tests/governance/`)

- `test_budget.py` — cap halt, zero denies all, `-1` unlimited, window rollover, persistence across instances.
- `test_governor.py` — CONTINUE on novelty, HALT on stall, HALT on dup ratio, SLOW on trend decline, bounded history, fail-closed HALT on unreadable DB.
- `test_approval.py` — submit→approve→audit rows, double-approve raises, reject, cancel, restart survival (new instance, same path), unknown kind.
- `test_killswitch.py` — arm/disarm + audit rows, `is_armed()` True on unreadable DB.
- `test_guard.py` — HALT on armed switch, HALT on exhausted budget, APPROVAL_REQUIRED/PENDING/OK paths, block events audited.
- `test_api.py` — `TestClient(create_app())` with monkeypatched tmp-backed singletons: status, spend-to-exhaust then guard deny, submit→list→approve flow, arm/disarm flow, audit rows present.

## Self-Review

- Fail-closed verified by tests (unreadable DB → armed/halted) — the "never trust unverified output" principle applied to the brakes themselves.
- No second audit path: every module imports `AuditChain`.
- Approval transitions are idempotency-safe (only PENDING can be decided).
- All tests ruff-clean (Phase 0A lesson applied).

## Not in this plan (explicit)

- **Flywheel wiring** (Phases 2): the loop doesn't call the brakes yet; `Guard` + `record_action` are the API it will use. Proved via tests and the HTTP surface, not a live loop.
- **Cockpit UI** for approvals (Phase 1) — API + CLI only.
- **Dockerfile** (owner chose `setup.sh` only).
- **Governor executing trims** — it only suggests `trim_candidates`; parking/deletion is flywheel behavior.
- **Operator auth on `/governance` control endpoints** — deferred to Phase 3 security hardening (loopback-bound; same as current control surface).
