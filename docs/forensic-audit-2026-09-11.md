# MSB v3 — Forensic Audit

**Date:** 2026-09-11
**Method:** 5 parallel agent sweeps (parked/blocked features, genuine code incompleteness, test-suite skip/xfail health, dead code / unregistered routes, docs-vs-reality drift) across all 370 source files, 287 test files, and the doc tree.
**Context:** Same night as blueprint steps 1–4 (frontier retirement, provider quarantine, Trinity retirement, launchd path fixes). This audit is the "what else is hiding" pass Wilson asked for directly, not part of the blueprint sequence.

---

## Already fixed tonight (committed, pushed)

- `CLAUDE.md` said "Primary LLM: DeepSeek V4-Flash" — flatly wrong since D1 (2026-09-09). Corrected, and noted the llama.cpp/Paseo quarantine defaults. This is the file read into context at the start of every session — the highest-value one-line fix of the audit.
- `README.md` claimed "seven built-in [cron] actions" — actually nine (`alert_check`, `model_keepalive` added tonight, not yet reflected).
- `docs/glossary.md` never defined **Paseo** at all despite it being one of two named quarantined subsystems, and its Ollama/llama.cpp entry still described the retired DeepSeek frontier seam as if live. Added a Paseo entry, corrected the frontier/llama.cpp description.

(Commit `e32b336`.)

---

## Real findings, not yet acted on

### 1. `src/msb_v3/meta/` — a fully-built subsystem with zero live importers

`contracts.py`, `loop.py`, `pipeline.py`, `scheduler.py`, `worker.py`, `verify.py` — an entire pipeline/scheduler/worker system. Grep for `msb_v3.meta.` outside its own directory: **zero hits** anywhere in `src/`. It's only reachable from its own test directory (`tests/meta/`) and a handful of unrelated test dirs. This is the single biggest "missing feature" candidate in the codebase — either it's finished work that was never wired into `api/app.py`/`cron/`/`agent/`, or it's abandoned exploration that should be archived. **This is a real decision only you can make** — I don't know which one it is, and guessing wrong either builds on top of dead work or deletes something you meant to finish.

### 2. `src/msb_v3/api/tenant_chat.py` — confirmed dead, not just parked

Never imported anywhere in `src/` except a stale build manifest. `app.py` mounts `api/chat.py`'s router for `/chat`, not this file. The handler itself does no real work even if it were wired in — it echoes the query back with `"model": "pending"`. Two independent agents confirmed this from different angles. Low risk today (genuinely unreachable), but it's a landmine for later: a future edit that wires it in via copy-paste would ship a fake chat endpoint without anyone reading its own warning comment. **Recommend deleting it** — it's dead, not paused work — but I didn't delete code you didn't ask me to remove without checking first.

### 3. `src/msb_v3/memory/store.py` (`MemoryStore`) — a half-finished migration

Carries an explicit `DeprecationWarning` ("use msb_v3.memory_fabric.store instead") but is still wired into `core/container.py`'s DI container, the live `/memory` router, and part of `/graph`. This is real, live, reachable code that's been marked for replacement but never actually finished migrating. It's the thing that's been firing the `DeprecationWarning` you've seen in test output all night. Not urgent (nothing's broken), but it's real technical debt with a clear finish line if you want it closed out.

### 4. The multimodal/speech "PARKED" blocker is likely stale

`triumvirate/multimodal_interfaces.py` (VisionClaw, HapticHeartbeat, SpeechFunctions — all canned/fixture responses right now) was parked 2026-08-17 citing `blocked_on: mac-mini-storage`. Checked tonight: **disk is at 63% used, 7.2GB free** — the disk-full state that justified the park no longer exists, and nothing in `docs/` has revisited the decision since. It's still flag-gated (`MSB_MULTIMODAL_ENABLED=1`, off by default) and has no core dependents, so it's not a live risk — but the *reason* it's parked no longer holds. Worth a deliberate "still parked, or ready to revisit" call rather than it just sitting there on a stale justification.

### 5. Five test skips that mask real failures instead of guarding against absent preconditions

Out of 55 total skip/xfail markers, 50 are legitimate (opt-in live tests, missing external tools, foreign-checkout guards — all self-documenting, no action needed). Five are not:

- **`tests/triumvirate/test_triumvirate_lifecycle.py:35`** — skips the entire plan→lock→verify lifecycle test under `pytest-xdist` (parallel runs) citing "flaky failures from concurrent server modifications." No confirmed serial-only CI lane covers it instead — meaning this integration path may not be getting verified in CI at all if xdist is in use there.
- **`tests/plei/test_phase2_capabilities.py:127,152,163,179`** (4 tests) — each calls `ingest_all(ROOT)` for real, then catches `MemoryError`/`OSError` *after* it happens and turns the crash into a skip. That's not an environment-absence guard, it's a real failure silently reclassified as "not applicable." A genuine regression in `ingest_all`'s resource usage would show up as a skip, not a failure — nobody would notice.

### 6. Two live, reachable modules with zero dedicated tests

`src/msb_v3/moie/meta_critic.py` (used by `core/calibration.py` and `moie/engine.py`) and `src/msb_v3/api/skill_router.py` (a registered, reachable `/skills` router) — both real, both live, neither has a test file. Not broken, just uncovered.

### 7. Correction to tonight's own "quarantine" framing

llama.cpp is **not** the same kind of "optional" as Anthropic/Paseo — it's imported by 6 modules (`client_factory.py`, `harnesses/base.py`, `agent/intent.py`, `agent/handle.py`, `agent/bridge_provider.py`, `api/models.py`) and remains a fully real, switchable backend. What actually got quarantined tonight was specifically the unconditional `/system/health` probe cost, not the provider itself — which is correct by design, just worth being precise about going forward so "quarantined" doesn't get misread as "disabled."

---

## Clean — no action needed

- **Genuine code incompleteness** (`NotImplementedError`, TODOs, bare-`pass` bodies, hidden 501s): zero real findings. Every hit traces to intentional patterns — verified ABC boilerplate with all subclasses overriding, self-referential linter source, defensive exception-swallowing with explicit "must not mask the real error" comments, one-line exception subclasses, a docstring example, and a graceful-degradation fallback for an optional dependency that's actually installed and running.
- **`energy_matrix/`** — confirmed accurately labeled EXPERIMENTAL; it's real, working telemetry-based scheduling logic with one live indirect dependent (flywheel health bridge), exactly as `docs/PRODUCTION-READINESS.md` claims.
- **`stage_0_knowledge_acquisition.py` / `transcript_requirements_extractor.py`** — fully implemented, well-tested, deliberately frozen by an explicit convergence rule (2026-08-16), no live dependents. Dead-but-parked on purpose, not a gap.
- **`uac/`** — a documented back-compat shim, not dead code; kept alive specifically for ~73 test-suite import sites while `src/` itself has fully migrated to `msb_ledger.*` directly.
- All 38 other API router files are correctly mounted in `app.py`.

---

## Suggested priority if you want to act on this

1. Delete `tenant_chat.py` (confirmed dead, zero risk) — or tell me to leave it if you had plans for it.
2. Decide: finish wiring `meta/`, or archive it. This is the one that actually changes what the codebase can do.
3. Re-litigate the multimodal storage blocker now that the disk isn't full — un-park, or re-justify staying parked.
4. Fix the two masking test skips (triumvirate xdist, PLEI resource-exhaustion catch) so real regressions can't hide as skips.
5. Finish the `MemoryStore` → `memory_fabric` migration, or explicitly decide it's staying dual-tracked.

None of these are urgent — nothing here is currently broken in production. They're the difference between "works" and "the codebase honestly reflects what's actually built and used."
