# Phase 2 — The Flywheel: Implementation Plan

**Blueprint:** `docs/blueprints/2026-08-11-adaptive-build-environment.md` (§0.5 engine + §Phase 2)
**Decision (owner, 2026-08-11):** build directly on `main`. **Scope for this plan: wire the loop's FIRST TURN end-to-end behind the Phase 0B brakes.** The cockpit build-mode (research panel, harvest, promote-inbox) is a follow-up plan.
**Gate:** `make test` + `make portability` green, ruff clean.

## The honest inventory (from a live survey, not memory)

| Blueprint asset | Reality found |
|---|---|
| MoIE swarm (stage 3 charge) | `sovereign_runtime/brain/moie_swarm.py` is a **placeholder stub** (`rounds: 0`) |
| Research runner (charge) | **REAL** — `SovereignResearchAssistant.run_full_pipeline()` emits `{slug}_UIM.json` in the blueprint's exact shape |
| Stage 0 knowledge acquisition (stage 5 scan) | REAL but profession-compliance focused — a poor fit for "scan papers"; the paper feed (Tavily/NotebookLM) wires in Phase 2b |
| Skills executor (stage 7 build) | REAL — `/skills/execute` |
| AxiomLibrary (stage 9 record) | REAL — versioned artifact store (`publish`) |
| Vault doc-trail (stage 9 record) | REAL — `~/Documents/Vault/20_Research/` structure |

**Consequence:** Phase 2 v1 makes the **loop mechanics and the brakes load-bearing and real**; the generative brain (charge/scan) is a **pluggable interface with a deterministic stub** (format-compatible with real UIMs) and an opt-in sovereign charger. This is the honest first turn: the state machine, gating, persistence, approvals, audit, and record all work for real; the research quality is a later wiring job. Ouroboros's whole point — don't let the loop run away — is exactly why the brakes get proven *before* the expensive brain is attached.

## Global Constraints

- **The engine never bypasses the brakes.** Every stage transition goes through `Guard.check_run`: kill switch (all stages), budget (1 iteration per stage; research_calls on charge+scan), approvals (stages 7 build / 8 combine / 9 record — the blueprint's irreversible list), governor (charge feeds convergence signals). A refusal parks or halts the turn with a reason — never continues.
- **Persisted + restart-surviving** — turn state in SQLite (`data/flywheel/turns.db`); a turn parked at WAITING_APPROVAL is found and resumed by any later engine instance.
- **Audited, never a black box** — every stage transition + every decision written to the UAC audit chain (component `flywheel`).
- **Deterministic with the stub charger** — same problem → byte-identical UIM (hash-seeded), so the first turn is reproducible and testable offline.
- Path-portable (all paths from `settings.msb_home`/`settings.vault_path`); no `/Users/...` literals.

## Task 1: `src/msb_v3/flywheel/` package

### `models.py`
- `STAGES` (blueprint order): `verify_novelty → draft_blueprint → charge → update_blueprint → scan_papers → surface_problems → build → combine → record`.
- `APPROVAL_STAGES = {build: "build", combine: "combine", record: "vault_write"}` (kinds from the approval queue).
- Budget: 1 `iterations` per stage; `charge`/`scan_papers` also spend 1 `research_calls`.
- Statuses: `PENDING, RUNNING, WAITING_APPROVAL, DONE, HALTED, BLOCKED, ALREADY_EXISTS, ERROR`.

### `chargers.py` — pluggable generative brain
- `StubCharger` — deterministic offline UIM (`{topic, slug, phase1: {assumption, inversion, predictions}, ok}` — the exact `SovereignResearchAssistant` shape), seeded from the problem hash. Same problem → identical output.
- `SovereignCharger` — opt-in: calls `SovereignResearchAssistant(topic, slug).run_inversion()` (writes the real `{slug}_UIM.json`).
- `PaperScanner` protocol + `StubScanner` — deterministic: surfaces problem candidates from the UIM, reports `papers_scanned: 0` with an explicit "real feed wires in Phase 2b" note (no fake "we scanned papers").

### `engine.py` — `FlywheelEngine`, the 9-stage state machine
- `start(problem, charger=..., skill=...)` — gated (`flywheel.start`, iterations) → PENDING turn row + audit.
- `run(turn_id)` — advance stage-by-stage; each stage: `guard.check_run(action=f"flywheel.{stage}", kind=..., budget_units=..., approval_id=..., signal=...)`; verdicts map to proceed / WAITING_APPROVAL (park, store the approval item id) / HALTED / BLOCKED. Each executed stage audited (`flywheel.stage.{stage}`), state persisted after every step.
- Stages:
  1. **verify_novelty** — vault semantic search for the problem (contained; offline ⇒ novelty 0.0); `novelty >= threshold` ⇒ `ALREADY_EXISTS`, turn stops before any build (the blueprint's "build only if not" gate — advisory; the approval gate remains the load-bearing one).
  2. **draft_blueprint** — write `runtime/flywheel/blueprints/{turn_id}.md`.
  3. **charge** — charger → UIM saved to `runtime/flywheel/uims/{turn_id}.json`; governor signal `{proposal_id: turn_id, novelty, duplicate_ratio}` feeds Ouroboros.
  4. **update_blueprint** — UIM findings appended to the blueprint.
  5. **scan_papers** — scanner (stub now) → notes.
  6. **surface_problems** — next-problem candidates from UIM + scan, saved to notes.
  7. **build** — approval-gated; writes the build manifest (`runtime/flywheel/builds/{turn_id}/`); optional skill execution via `/skills/execute` when the turn names one.
  8. **combine** — approval-gated; deterministic cross-domain merge with the newest *other* research UIM → `runtime/flywheel/combines/{turn_id}.md`.
  9. **record** — approval-gated; vault doc-trail (`{vault}/20_Research/flywheel/{turn_id}.md`) + `AxiomLibrary.publish(ArtifactRecord(...))` + audit.
- `approve(turn_id)` — approve this turn's pending approval items (operator) then resume; `resume(turn_id)` — re-run a parked turn; `get/list/state`.

### `cli.py` + `__main__.py`
`python -m msb_v3.flywheel turn "PROBLEM" [--charger stub|sovereign] [--skill NAME] | status | approve <id> | resume <id> | show <id>` — mirrors the governance/ops CLI style.

## Task 2: HTTP surface (`src/msb_v3/api/flywheel.py`)

- `POST /flywheel/turn` (problem, charger, skill) — starts the turn as a background task, returns immediately.
- `GET /flywheel/turns` — list; `GET /flywheel/turns/{id}` — state.
- `POST /flywheel/turns/{id}/approve` / `resume` — operator controls (like the governance controls, loopback-bound).
- Module-level engine singleton (monkeypatched in tests, governance pattern); mounted in `app.py`.

## Task 3: cockpit FLYWHEEL panel

`/cockpit/api` gains a `flywheel` panel (in-process: turns count, statuses, newest turn's stage/status); the page gains a FLYWHEEL card. Read-only, per Phase 1's contract.

## Task 4: Makefile + docs

- `make flywheel-turn PROBLEM="..." CHARGER=stub`, `make flywheel-status`, `make flywheel-approve ID=...`.
- CLAUDE.md Flywheel section (endpoints, CLI, brake semantics, stub-vs-sovereign charger).

## Task 5: tests (`tests/flywheel/`)

- **Happy path** (stub): turn parks at build → `approve` → parks at combine → approve → record → approve → **DONE**; assert UIM saved, blueprint + vault doc + axiom record exist, every stage audited.
- **Kill switch** — `start` refused (BLOCKED).
- **Budget** — `research_calls: 0` ⇒ charge halts (HALTED).
- **Novelty gate** — monkeypatched high novelty ⇒ ALREADY_EXISTS, nothing built.
- **Restart survival** — park, new engine instance, same DB, approve+resume ⇒ DONE.
- **Governor engagement** — governor with `stall_limit=1`/high floor ⇒ charge HALTED by Ouroboros.
- **Determinism** — same problem twice ⇒ identical UIM phase1.
- **API** — POST turn (202), GET turns, approve ⇒ DONE (poll).

## Self-Review

- The brakes are load-bearing by construction: every stage transition is a guard call; refusals park/halt with reasons; approvals are the only path past stages 7–9.
- Turn state is durable and resumable; nothing is lost between restarts.
- The stub is honest: it produces format-compatible UIMs and says *"stub"* in the scan notes — no fabricated "we scanned N papers".

## Not in this plan (explicit)

- **Real MoIE charge + real paper scanner** (Tavily/NotebookLM/stage-0) — Phase 2b, the generative-brain wiring behind the now-proven brakes.
- **Cockpit build-mode controls** (research panel, harvest, promote-inbox) — follow-up plan.
- **Scheduling/auto-run** of turns — the loop does not turn itself yet; the owner drives it (approvals are part of the loop, not a bypass).
- **Governor executing trims** — it only halts/slows and records signals here.
