# Mission Harness (Phase 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the harness registry + worker harness base that pins invariants H1–H5 from `docs/blueprints/2026-09-25-agent-control-plane.md` §6/§12. This is Phase 2 of the control-plane blueprint; Phase 1 (mission spine) is landed on `main` at `279359a` (`feat/mission-spine` synced).

**Architecture:** Two new modules extend the existing seams without forking them:

- `src/msb_v3/harnesses/registry.py`: the governing record of every harness — identity binding (H1), allowed_files boundary (H2), merge-authority absence (H3), budget pointer (H4), workspace binding (H5). Every registration and revocation is written to the audit chain (`component="harnesses"`, events `harness.registered` / `harness.revoked`) and only then to SQLite (`runtime/harnesses.db`), same atomic discipline as `mission/store.py`. Nothing here authenticates callers; it records what the operator approved.

- `src/msb_v3/harnesses/worker.py`: the worker harness base that every task dispatch uses. It owns `harness_id + identity + allowed_files + budget ledger + isolated worktree + artifact collection`. It enforces H2 (write outside `allowed_files` → BLOCK), H3 (any merge attempt → BLOCK, no harness ever holds merge authority), H4 (budget exhaustion → stop, record partial state, never degrade to a cheaper model), and H5 (parallel worktrees only when the plan marks them file-disjoint). It reuses `agent/identity.py` (fingerprint), `factory/builders.py` (`create_worktree`, `compute_changes`), and `governance/budget.py` (`BudgetLedger`) — no new copy of any of them.

- `src/msb_v3/harnesses/base.py`: stays the chat surface. Worker-specific enforcement lives in `worker.py` so the mission gate and factory do not change; later phases add the capability registry/router (Phase 3) and the PM/Planner/Verifier harnesses (Phases 4–8) on the same seams.

Invariants H1–H5 are frozen the way the mission matrix is: each pinned by a failing test in this plan. If a test disagrees with H1–H5, stop and report — do not edit the test to make it pass.

**Tech Stack:** Python 3.11+, sqlite3, `msb_ledger.audit_chain.AuditChain` / `AuditChainLike`, `msb_ledger.audit_v2` (canonicalize / sha256_hex), `msb_v3.agent.identity.AgentRegistry`, `msb_v3.governance.budget.BudgetLedger`, `msb_v3.factory.builders.create_worktree`, pytest, hypothesis (already installed).

## Global Constraints

- Work only in the worktree `~/projects/AI-Agents/msb-v3-mission-harness`, on branch `feat/mission-harness`. Never touch `~/projects/AI-Agents/msb-v3` (main checkout) or `~/projects/AI-Agents/msb-v3-mission` (Phase 1 worktree).
- **Every python command is prefixed with `PYTHONPATH=src`.** The installed `msb_v3` package points at the main checkout. Without the prefix, tests silently import `main`'s code instead of this worktree's, so they "pass" while testing the wrong files.
- Python: `/opt/homebrew/Caskroom/miniforge/base/bin/python` (the `python` on PATH). There is no `.venv`.
- **No `git commit`, no `git push`, no `git merge`, no checkout of `main`.** `FREEBUFF.md` forbids commits outright, and blueprint D-3 says workers write in worktrees while only the gate and Wilson merge. **Skip every "Commit" step below.** They are there for an executor that is allowed to commit. The work stays in the worktree, and Task 5 Step 6 turns it into a patch.
- Do not add dependencies. `hypothesis` is already installed.
- H1–H5 are frozen. If a test disagrees with H1–H5, stop and report. Do not weaken the test.
- Do not weaken a failing test. If a test fails after the implementation step, the implementation is wrong or the plan is. Record it in `notes/freebuff-observation.md` and stop.
- `ruff check src tests scripts` and `mypy src` must stay clean. `mypy` is run as `PYTHONPATH=src mypy src` with the project's `mypy.ini` (strict, but `src/msb_v3/harnesses` follows the same `ignore_errors = false` as `src/msb_v3/mission` — no new `ignore_missing_imports`).
- Disk on `/` is 99% full (217 MiB free at plan time). The portability gate (`make portability` → rsync `.mypy_cache`) fails with `No space left on device`. Run portability with `MSB_SKIP_PORTABILITY=1` when reclaim is not yet done; the gate otherwise stays bypassed via the Makefile flag, same as Phase 1 landing.

## File Structure

| File | Responsibility |
|---|---|
| `src/msb_v3/harnesses/registry.py` | `HarnessSpec`, `HarnessRecord`, `HarnessRegistry`: identity binding (H1), allowed_files + merge-authority absence (H2/H3), budget pointer (H4), workspace binding (H5); chain-evidenced SQLite |
| `src/msb_v3/harnesses/worker.py` | `WorkerHarness(BaseHarness)`, `WorkerResult`, `FileBoundaryError`, `disjoint_allowed_files`: enforces H2–H5 at execution time, per-worker worktree, budget stop with partial state, artifact collection |
| `src/msb_v3/harnesses/__init__.py` | public exports (registry + worker + existing `base.py` re-exports) |
| `src/msb_v3/harnesses/base.py` | unchanged (chat surface) — worker logic does not edit it |
| `tests/harnesses/test_harness_registry.py` | H1–H3 registry invariants + chain evidence |
| `tests/harnesses/test_harness_worker.py` | H2–H4 worker enforcement + budget partial-state |
| `tests/harnesses/test_harness_parallel.py` | H5 file-disjoint + per-worker worktree |
| `docs/SURFACE.md` | add `msb_v3/harnesses` row remains OPTIONAL with Phase 2 justification (no new router) |
| `docs/what-msb-v3-is.md` | re-measured scale counts (the records gate requires it) |

Test files carry a `test_harness_` prefix so basenames do not collide with other `test_registry.py` / `test_worker.py` suites. Existing `tests/harnesses/test_chat_actor.py` stays green — chat surface is not touched except for the re-export.

---

### Task 0: Preflight

**Files:** none

- [ ] **Step 1: Confirm the worktree and branch**

Run: `cd ~/projects/AI-Agents/msb-v3-mission-harness && git branch --show-current && git status --short`
Expected: `feat/mission-harness`, and apart from this plan file an empty status. If the worktree does not exist, create it from `main` at `279359a`: `git worktree add ~/projects/AI-Agents/msb-v3-mission-harness -b feat/mission-harness 279359a`

- [ ] **Step 2: Confirm imports resolve to this worktree**

Run: `PYTHONPATH=src python -c "import msb_v3, msb_ledger; print(msb_v3.__file__); print(msb_ledger.__file__)"`
Expected: both paths start with `/Users/lordwilson/projects/AI-Agents/msb-v3-mission-harness/src/`. If either shows `/msb-v3/src/`, stop: every later result would be meaningless.

- [ ] **Step 3: Record the base**

Run: `git rev-parse HEAD`
Put the value in `result.json` as `base_head_seen`. Expected: `279359a` (Phase 1 landed).

---

### Task 1: Harness identity binding — H1

**Files:**
- Create: `src/msb_v3/harnesses/registry.py`
- Create: `src/msb_v3/harnesses/__init__.py` (or extend existing — it already re-exports `base.py`; add registry + worker exports in Task 5)
- Test: `tests/harnesses/test_harness_registry.py`

**Interfaces:**
- Consumes: `AgentIdentity`, `AgentRegistry` (`agent/identity.py`), `AuditChainLike`, `settings.db_path`.
- Produces:
  - `HARNESS_COMPONENT = "harnesses"`, `HARNESS_OPERATORS_ENV = "MSB_HARNESS_OPERATORS"` (mirrors `mission/store.py`'s `OPERATORS_ENV` — operator set gates registration/revocation)
  - `HarnessError(RuntimeError)` — refused operations; nothing recorded
  - `HarnessSpec` (frozen dataclass): `harness_id: str`, `agent_id: str`, `allowed_files: tuple[str, ...]` (glob-ish repo-relative paths, e.g. `("src/msb_v3/**", "tests/**")` — at least one entry, no empty strings), `budget_category: str`, `budget_limit: int`, `workspace: str | None` (filled on bind), `fingerprint: str` (copied from `AgentIdentity.fingerprint` at registration time)
  - `HarnessRecord` (frozen dataclass): `spec: HarnessSpec`, `created_at: str`, `revoked: bool`, `revoked_at: str | None`
  - `HarnessRegistry(db_path: str | None = None, *, chain: AuditChainLike | None = None, operators: frozenset[str] | set[str] | None = None, agent_registry: AgentRegistry | None = None)` with methods:
    - `register(spec: HarnessSpec, *, actor: str, approved_by: str | None = None) -> HarnessRecord` — validates `agent_id` exists and is not revoked in `AgentRegistry`, copies its `fingerprint`; validates `allowed_files` non-empty and each entry is a non-empty repo-relative path without `..` or absolute `/`; validates `budget_limit >= 0` (`0` = deny everything per `BudgetLedger` semantics); validates `harness_id` non-empty and unique; checks `approved_by` is in configured operators (fail closed — no operators means every registration is refused); writes chain event `harness.registered` then SQLite, atomic (chain refusal → `HarnessError("audit chain refused …; nothing was recorded")`).
    - `revoke(harness_id: str, *, actor: str, approved_by: str | None = None) -> HarnessRecord`
    - `get(harness_id: str) -> HarnessRecord` — raises `HarnessError("unknown harness …")`
    - `assert_identity(harness_id: str, agent_id: str) -> None` — raises `HarnessError("identity mismatch …")` when the harness's bound `agent_id`/`fingerprint` does not match the supplied `agent_id`'s current `AgentIdentity.fingerprint` (H1)
    - `list(include_revoked: bool = False) -> list[HarnessRecord]`
    - `operators_from_env() -> frozenset[str]` (same contract as `mission.store.operators_from_env` but env var `MSB_HARNESS_OPERATORS`)
  - Chain events on component `"harnesses"`: `harness.registered` (payload: `harness_id`, `agent_id`, `fingerprint`, `allowed_files`, `budget_category`, `budget_limit`), `harness.revoked`.

Invariants this task pins (each a test below):
1. H1: a harness cannot assume another harness's identity — `assert_identity` fails when the caller supplies a different `agent_id`, and when the bound agent's fingerprint drifts (capability/tenant/autonomy change) the harness is stale until re-registered.
2. Every write commits only after the chain accepted its event; chain refusal records nothing.
3. Registration/revocation need approval from a configured operator; no operators → fail closed.
4. A revoked harness cannot be used (`assert_identity` on a revoked harness raises).
5. `harness_id` uniqueness and `allowed_files` validation are enforced.
6. SQLite triggers refuse UPDATE/DELETE on the harness tables (append-only + revoke is an UPDATE of the `revoked` column — allowed only via `revoke()`, not direct SQL; the trigger allows that single column's transition 0→1 but refuses any other UPDATE).

- [ ] **Step 1: Write the failing test** at `tests/harnesses/test_harness_registry.py`

```python
"""Harness registry — H1 identity binding (blueprint 2026-09-25 §6, Phase 2)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from msb_ledger.audit_chain import AuditChain
from msb_v3.agent.identity import AgentIdentity, AgentRegistry
from msb_v3.harnesses.registry import (
    HARNESS_COMPONENT,
    HarnessError,
    HarnessRecord,
    HarnessRegistry,
    HarnessSpec,
)


class RefusingChain:
    def append(self, component: str, event_type: str, payload: dict[str, Any]) -> None:
        raise RuntimeError("chain unavailable")


@pytest.fixture
def agent_registry(tmp_path: Path) -> AgentRegistry:
    reg = AgentRegistry(str(tmp_path / "agents.db"))
    for aid in ("agent.hermes", "agent.freebuff", "agent.claude"):
        reg.register(AgentIdentity(agent_id=aid, name=aid, kind="local", provider_id="local.slice", granted_capabilities=("chat",)))
    return reg


@pytest.fixture
def chain(tmp_path: Path) -> AuditChain:
    return AuditChain(str(tmp_path / "audit.db"), allow_keyless=True)


@pytest.fixture
def registry(tmp_path: Path, chain: AuditChain, agent_registry: AgentRegistry) -> HarnessRegistry:
    return HarnessRegistry(str(tmp_path / "harnesses.db"), chain=chain, operators={"wilson"}, agent_registry=agent_registry)


def events(chain: AuditChain, event_type: str) -> list[Any]:
    return [r for r in chain.get_chain(HARNESS_COMPONENT) if r.event_type == event_type]


def _spec(harness_id: str = "harness-1", agent_id: str = "agent.hermes") -> HarnessSpec:
    return HarnessSpec(harness_id=harness_id, agent_id=agent_id, allowed_files=("src/msb_v3/**", "tests/**"), budget_category="tokens", budget_limit=1000)


# -- H1 ---------------------------------------------------------------------


def test_register_binds_fingerprint_and_assert_identity_passes(registry: HarnessRegistry, agent_registry: AgentRegistry, chain: AuditChain) -> None:
    rec = registry.register(_spec(), actor="wilson", approved_by="wilson")
    assert rec.spec.fingerprint == agent_registry.get("agent.hermes").fingerprint
    registry.assert_identity("harness-1", "agent.hermes")  # does not raise
    assert events(chain, "harness.registered")[0].payload["fingerprint"] == rec.spec.fingerprint


def test_harness_cannot_assume_another_harness_identity(registry: HarnessRegistry) -> None:
    registry.register(_spec(), actor="wilson", approved_by="wilson")
    with pytest.raises(HarnessError, match="identity mismatch"):
        registry.assert_identity("harness-1", "agent.freebuff")
    with pytest.raises(HarnessError, match="identity mismatch"):
        registry.assert_identity("harness-1", "agent.claude")


def test_fingerprint_drift_is_detected_until_reregistered(registry: HarnessRegistry, agent_registry: AgentRegistry) -> None:
    registry.register(_spec(), actor="wilson", approved_by="wilson")
    # Drift the agent's grant (new capability → new fingerprint).
    drifted = AgentIdentity(agent_id="agent.hermes", name="agent.hermes", kind="local", provider_id="local.slice", granted_capabilities=("chat", "vault_write"))
    agent_registry.register(drifted)
    with pytest.raises(HarnessError, match="fingerprint drift"):
        registry.assert_identity("harness-1", "agent.hermes")
    # Re-registering the harness rebinds the new fingerprint.
    registry.revoke("harness-1", actor="wilson", approved_by="wilson")
    registry.register(_spec(), actor="wilson", approved_by="wilson")
    registry.assert_identity("harness-1", "agent.hermes")


def test_revoked_harness_cannot_be_used(registry: HarnessRegistry) -> None:
    registry.register(_spec(), actor="wilson", approved_by="wilson")
    registry.revoke("harness-1", actor="wilson", approved_by="wilson")
    with pytest.raises(HarnessError, match="revoked"):
        registry.assert_identity("harness-1", "agent.hermes")
    assert registry.get("harness-1").revoked is True


# -- governance -------------------------------------------------------------


def test_registration_needs_configured_operator(tmp_path: Path, chain: AuditChain, agent_registry: AgentRegistry) -> None:
    reg = HarnessRegistry(str(tmp_path / "h.db"), chain=chain, operators=set(), agent_registry=agent_registry)
    with pytest.raises(HarnessError, match="configured operator"):
        reg.register(_spec(), actor="wilson", approved_by="wilson")
    with pytest.raises(HarnessError, match="configured operator"):
        reg.register(_spec(), actor="wilson", approved_by=None)


def test_chain_refusal_records_nothing(tmp_path: Path, agent_registry: AgentRegistry) -> None:
    reg = HarnessRegistry(str(tmp_path / "h.db"), chain=RefusingChain(), operators={"wilson"}, agent_registry=agent_registry)  # type: ignore[arg-type]
    with pytest.raises(HarnessError, match="audit chain refused"):
        reg.register(_spec(), actor="wilson", approved_by="wilson")
    conn = sqlite3.connect(tmp_path / "h.db")
    try:
        assert conn.execute("SELECT COUNT(*) FROM harnesses").fetchone()[0] == 0
    finally:
        conn.close()


def test_unknown_harness_and_validation(registry: HarnessRegistry) -> None:
    with pytest.raises(HarnessError, match="unknown harness"):
        registry.get("nope")
    with pytest.raises(HarnessError, match="unknown harness"):
        registry.assert_identity("nope", "agent.hermes")
    with pytest.raises(HarnessError, match="allowed_files"):
        registry.register(HarnessSpec(harness_id="h2", agent_id="agent.hermes", allowed_files=(), budget_category="tokens", budget_limit=10), actor="wilson", approved_by="wilson")
    with pytest.raises(HarnessError, match="allowed_files"):
        registry.register(HarnessSpec(harness_id="h3", agent_id="agent.hermes", allowed_files=("../escape",), budget_category="tokens", budget_limit=10), actor="wilson", approved_by="wilson")


def test_harness_id_uniqueness(registry: HarnessRegistry) -> None:
    registry.register(_spec("h1"), actor="wilson", approved_by="wilson")
    with pytest.raises(HarnessError, match="already registered"):
        registry.register(_spec("h1"), actor="wilson", approved_by="wilson")


def test_tables_are_append_only_except_revoke(tmp_path: Path, registry: HarnessRegistry) -> None:
    registry.register(_spec(), actor="wilson", approved_by="wilson")
    conn = sqlite3.connect(tmp_path / "harnesses.db")
    try:
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("DELETE FROM harnesses")
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("UPDATE harnesses SET allowed_files = '[]' WHERE harness_id = 'harness-1'")
        # revoke() is the only allowed mutation (0→1 on revoked).
        registry.revoke("harness-1", actor="wilson", approved_by="wilson")
        assert registry.get("harness-1").revoked is True
    finally:
        conn.close()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `PYTHONPATH=src python -m pytest -q tests/harnesses/test_harness_registry.py -p no:cacheprovider`
Expected: `ModuleNotFoundError: No module named 'msb_v3.harnesses.registry'`.

- [ ] **Step 3: Write the implementation** at `src/msb_v3/harnesses/registry.py`

```python
"""Harness registry — H1 identity binding (blueprint §6, Phase 2).

Every harness is bound at registration time to an AgentIdentity's fingerprint.
assert_identity() checks that binding on every dispatch, so Hermes cannot become
FreeBuff and a drifted grant is detectable until the harness is re-registered.
Writes are atomic: chain-then-commit, same discipline as mission/store.py.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from msb_ledger.audit_chain import AuditChainLike
from msb_ledger.chain_anchor import anchored_chain_from_env
from msb_v3.agent.identity import AgentRegistry
from msb_v3.core.config import settings

_DB = Path(settings.db_path).parent / "runtime" / "harnesses.db"
HARNESS_COMPONENT = "harnesses"
HARNESS_OPERATORS_ENV = "MSB_HARNESS_OPERATORS"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS harnesses (
    harness_id     TEXT PRIMARY KEY,
    agent_id       TEXT NOT NULL,
    fingerprint    TEXT NOT NULL,
    allowed_files  TEXT NOT NULL,
    budget_category TEXT NOT NULL,
    budget_limit   INTEGER NOT NULL,
    workspace      TEXT,
    created_at     TEXT NOT NULL,
    revoked        INTEGER NOT NULL DEFAULT 0,
    revoked_at     TEXT
);
CREATE TRIGGER IF NOT EXISTS harnesses_no_delete
    BEFORE DELETE ON harnesses
    BEGIN SELECT RAISE(ABORT, 'harnesses are append-only; use revoke()'); END;
CREATE TRIGGER IF NOT EXISTS harnesses_no_update
    BEFORE UPDATE ON harnesses
    WHEN OLD.revoked = 1 OR NEW.revoked != 1 OR OLD.harness_id != NEW.harness_id
         OR OLD.agent_id != NEW.agent_id OR OLD.fingerprint != NEW.fingerprint
         OR OLD.allowed_files != NEW.allowed_files
    BEGIN SELECT RAISE(ABORT, 'harnesses are immutable except revoke 0→1'); END;
"""

class HarnessError(RuntimeError): ...

def operators_from_env() -> frozenset[str]:
    raw = os.getenv(HARNESS_OPERATORS_ENV, "")
    return frozenset(p.strip() for p in raw.split(",") if p.strip())

@dataclass(frozen=True)
class HarnessSpec:
    harness_id: str
    agent_id: str
    allowed_files: tuple[str, ...]
    budget_category: str = "tokens"
    budget_limit: int = 1000
    workspace: str | None = None
    fingerprint: str = ""

@dataclass(frozen=True)
class HarnessRecord:
    spec: HarnessSpec
    created_at: str
    revoked: bool
    revoked_at: str | None

# ... full implementation follows the mission/store.py atomic pattern ...
# register() validates agent_id exists & not revoked, copies fingerprint,
# validates allowed_files (non-empty, repo-relative, no ".." or leading "/"),
# validates budget_limit >= 0, checks approved_by in operators (fail closed),
# then BEGIN IMMEDIATE → INSERT → chain.append("harnesses","harness.registered",payload) → COMMIT.
# revoke() mirrors it with event "harness.revoked".
# assert_identity() loads the harness, refuses if revoked, compares supplied
# agent_id to spec.agent_id and current AgentIdentity.fingerprint to spec.fingerprint.
```

Sketch above; the real file implements the full atomic `_atomic()` helper, `_init_db()`, and the validation helpers `_validate_allowed_files()` and `_validate_spec()`.

- [ ] **Step 4: Run it to verify it passes**

Run: `PYTHONPATH=src python -m pytest -q tests/harnesses/test_harness_registry.py -p no:cacheprovider`
Expected: `11 passed` (or the count the final file actually yields — every test above green).

- [ ] **Step 5: ruff + mypy**

Run: `PYTHONPATH=src python -m ruff check src/msb_v3/harnesses tests/harnesses && PYTHONPATH=src python -m mypy src/msb_v3/harnesses`

---

### Task 2: Worker harness base — H2 file boundary + H3 no merge authority

**Files:**
- Create: `src/msb_v3/harnesses/worker.py`
- Test: `tests/harnesses/test_harness_worker.py`

**Interfaces:**
- Consumes: `HarnessRegistry` (Task 1), `BudgetLedger` (`governance/budget.py`), `create_worktree` / `compute_changes` (`factory/builders.py`), `AgentRegistry` (for H1 check), `settings`.
- Produces:
  - `FileBoundaryError(RuntimeError)` — raised internally when a write escapes `allowed_files`; surfaced as a BLOCK result, never a warning
  - `WorkerResult` (frozen dataclass): `ok: bool`, `event: str` (`"harness:completed"` | `"harness:blocked:file_boundary"` | `"harness:blocked:merge_authority"` | `"harness:budget_exhausted"` | `"harness:failed"`), `harness_id: str`, `artifacts: dict[str, Any]` (each with `sha256`), `changed_files: list[str]`, `diff: str` (bounded, from `compute_changes`), `telemetry: dict[str, Any]`, `error: str | None`
  - `WorkerHarness(BaseHarness)` — `__init__(self, harness_id: str, *, registry: HarnessRegistry, budget_ledger: BudgetLedger | None = None, agent_registry: AgentRegistry | None = None)`; `execute(self, goal: str, context: dict[str, Any] | None = None, *, session: str = "default") -> WorkerResult` (synchronous for Phase 2 — async workers arrive with Hermes/FreeBuff providers in Phase 6; the harness still enforces the same invariants).
  - `enforce_allowed_files(changed_files: list[str], allowed_files: tuple[str, ...]) -> None` — pure helper; raises `FileBoundaryError` with the violating path when any `changed_files` entry does not match any `allowed_files` glob (fnmatch, repo-relative).
  - `MERGE_MARKERS: frozenset[str]` — strings that indicate merge authority (`"git merge"`, `"git push"`, `"gh pr merge"`, etc.); any goal or context that contains one is a BLOCK.

Rules pinned here:
1. H2: any file outside `allowed_files` → `WorkerResult(ok=False, event="harness:blocked:file_boundary")`, no files are kept, the violation is recorded. The harness never warns and carries on.
2. H3: no harness holds merge authority — any goal/context containing a merge marker → `WorkerResult(ok=False, event="harness:blocked:merge_authority")`, even if the files would otherwise be allowed. The worker never runs `git merge`/`push`.
3. H1 is re-checked at dispatch: `registry.assert_identity(harness_id, agent_id)` where `agent_id` comes from `registry.get(harness_id).spec.agent_id` — a harness that drifted or was revoked is refused before any work starts.
4. Artifact collection: every file under `allowed_files` that the worker created/modified is hashed (`sha256_hex` via `msb_ledger.audit_v2`) and listed in `artifacts` (path → `{sha256, bytes}`); the diff is bounded (`max_diff_bytes=8000`, same as `factory/builders.py`).

- [ ] **Step 1: Write the failing test** at `tests/harnesses/test_harness_worker.py`

```python
"""Worker harness — H2 file boundary + H3 no merge authority (Phase 2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from msb_ledger.audit_chain import AuditChain
from msb_v3.agent.identity import AgentIdentity, AgentRegistry
from msb_v3.governance.budget import BudgetLedger
from msb_v3.harnesses.registry import HarnessRegistry, HarnessSpec
from msb_v3.harnesses.worker import WorkerHarness, WorkerResult


@pytest.fixture
def agent_registry(tmp_path: Path) -> AgentRegistry:
    reg = AgentRegistry(str(tmp_path / "agents.db"))
    reg.register(AgentIdentity(agent_id="agent.hermes", name="h", kind="local", provider_id="local.slice", granted_capabilities=("chat",)))
    return reg


@pytest.fixture
def harness_registry(tmp_path: Path, agent_registry: AgentRegistry) -> HarnessRegistry:
    chain = AuditChain(str(tmp_path / "audit.db"), allow_keyless=True)
    reg = HarnessRegistry(str(tmp_path / "h.db"), chain=chain, operators={"wilson"}, agent_registry=agent_registry)
    reg.register(HarnessSpec(harness_id="hermes-1", agent_id="agent.hermes", allowed_files=("src/msb_v3/harnesses/**", "tests/harnesses/**"), budget_category="tokens", budget_limit=1000), actor="wilson", approved_by="wilson")
    return reg


@pytest.fixture
def ledger(tmp_path: Path) -> BudgetLedger:
    return BudgetLedger(db_path=str(tmp_path / "budget.db"), limits={"tokens": 1000}, window_s=3600)


def test_allowed_files_are_enforced(tmp_path: Path, harness_registry: HarnessRegistry, ledger: BudgetLedger, agent_registry: AgentRegistry) -> None:
    h = WorkerHarness("hermes-1", registry=harness_registry, budget_ledger=ledger, agent_registry=agent_registry)
    # Simulate a worker that writes outside allowed_files by passing changed_files via context.
    result = h.execute("do work", context={"changed_files": ["src/msb_v3/harnesses/worker.py", "src/msb_v3/api/auth.py"]})
    assert result.ok is False
    assert result.event == "harness:blocked:file_boundary"
    assert "src/msb_v3/api/auth.py" in (result.error or "")


def test_writes_inside_allowed_files_pass(tmp_path: Path, harness_registry: HarnessRegistry, ledger: BudgetLedger, agent_registry: AgentRegistry) -> None:
    h = WorkerHarness("hermes-1", registry=harness_registry, budget_ledger=ledger, agent_registry=agent_registry)
    result = h.execute("do work", context={"changed_files": ["src/msb_v3/harnesses/worker.py"]})
    assert result.ok is True
    assert result.event == "harness:completed"


def test_merge_authority_is_never_granted(tmp_path: Path, harness_registry: HarnessRegistry, ledger: BudgetLedger, agent_registry: AgentRegistry) -> None:
    h = WorkerHarness("hermes-1", registry=harness_registry, budget_ledger=ledger, agent_registry=agent_registry)
    for goal in ("git merge main", "please git push", "gh pr merge --auto"):
        result = h.execute(goal, context={"changed_files": ["src/msb_v3/harnesses/worker.py"]})
        assert result.ok is False
        assert result.event == "harness:blocked:merge_authority"
        assert "merge" in (result.error or "").lower()


def test_revoked_or_drifted_identity_is_refused_before_work(tmp_path: Path, harness_registry: HarnessRegistry, ledger: BudgetLedger, agent_registry: AgentRegistry) -> None:
    h = WorkerHarness("hermes-1", registry=harness_registry, budget_ledger=ledger, agent_registry=agent_registry)
    harness_registry.revoke("hermes-1", actor="wilson", approved_by="wilson")
    result = h.execute("do work", context={"changed_files": ["src/msb_v3/harnesses/worker.py"]})
    assert result.ok is False
    assert "revoked" in (result.error or "").lower() or "identity" in (result.error or "").lower()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `PYTHONPATH=src python -m pytest -q tests/harnesses/test_harness_worker.py -p no:cacheprovider`
Expected: `ModuleNotFoundError: No module named 'msb_v3.harnesses.worker'`.

- [ ] **Step 3: Write the implementation** at `src/msb_v3/harnesses/worker.py`

Key points:
- `WorkerHarness` holds `harness_id`, `registry`, `budget_ledger`, `agent_registry` (or resolves `AgentRegistry()` lazily). `execute()` first calls `registry.assert_identity(harness_id, spec.agent_id)` — H1. Then scans `goal` + `str(context)` for any `MERGE_MARKERS` — H3 BLOCK before any filesystem work. Then validates `changed_files` against `spec.allowed_files` via `enforce_allowed_files` — H2 BLOCK. Only then does it create a worktree (`create_worktree(repo_path)`) when `context` does not already carry `changed_files` (the test seam); in production it copies the repo, lets the worker write, then `compute_changes` to get the real `changed_files`/`diff` and re-enforces H2 against the real diff. Artifact collection hashes each changed file with `sha256_hex`.
- No `subprocess` for Phase 2 — the "worker" is the harness itself; the goal is not executed as code, only bounded and evidenced. Phase 6 replaces the seam with real providers.

- [ ] **Step 4: Run it to verify it passes**

Run: `PYTHONPATH=src python -m pytest -q tests/harnesses/test_harness_worker.py -p no:cacheprovider`
Expected: `4 passed`.

---

### Task 3: Budget exhaustion — H4

**Files:**
- Extend: `src/msb_v3/harnesses/worker.py` (budget gate)
- Test: extend `tests/harnesses/test_harness_worker.py` or add `tests/harnesses/test_harness_budget.py` — either is fine, but keep H4's budget test separate so the invariant is findable.

**Interfaces:**
- Consumes: `BudgetLedger` (`governance/budget.py`), `HarnessRegistry` (budget_category/budget_limit per harness).
- Produces (on `WorkerHarness`):
  - Budget check before work: `ledger.spend(budget_category, estimated_cost)` where `estimated_cost` defaults to `int(context.get("estimated_tokens", 1))` (at least 1). When `spend()` returns `False`, the harness stops immediately with `WorkerResult(ok=False, event="harness:budget_exhausted", error="budget exhausted: <category> …", artifacts={"partial": True, "spent": …, "limit": …, "remaining": …}, telemetry={"budget_category": …, "budget_spent": …, "budget_limit": …})`. No fallback to another model/provider is attempted — the harness records the partial state and halts (H4).
  - When the budget check passes, the harness proceeds to H2/H3 enforcement and records `telemetry["budget_spent"]` / `telemetry["budget_remaining"]` on the completed result as well.

Rules pinned:
1. H4: budget exhaustion stops the harness and records the partial state; it never silently degrades to a cheaper model (the test asserts no second `spend()` on a different category is attempted).
2. A zero cap denies everything; an unlimited cap (`limit < 0`) never blocks — mirrors `BudgetLedger` semantics.
3. The partial state is observable: `artifacts["partial"] is True` and `telemetry` carries the ledger's `state()[category]`.

- [ ] **Step 1: Write the failing test** at `tests/harnesses/test_harness_budget.py`

```python
"""Worker harness — H4 budget exhaustion (Phase 2)."""

from pathlib import Path

import pytest

from msb_ledger.audit_chain import AuditChain
from msb_v3.agent.identity import AgentIdentity, AgentRegistry
from msb_v3.governance.budget import BudgetLedger
from msb_v3.harnesses.registry import HarnessRegistry, HarnessSpec
from msb_v3.harnesses.worker import WorkerHarness


@pytest.fixture
def harness_with_tiny_budget(tmp_path: Path):
    agent_reg = AgentRegistry(str(tmp_path / "agents.db"))
    agent_reg.register(AgentIdentity(agent_id="agent.hermes", name="h", kind="local", provider_id="local.slice", granted_capabilities=("chat",)))
    chain = AuditChain(str(tmp_path / "audit.db"), allow_keyless=True)
    reg = HarnessRegistry(str(tmp_path / "h.db"), chain=chain, operators={"wilson"}, agent_registry=agent_reg)
    reg.register(HarnessSpec(harness_id="hermes-1", agent_id="agent.hermes", allowed_files=("src/**",), budget_category="tokens", budget_limit=2), actor="wilson", approved_by="wilson")
    ledger = BudgetLedger(db_path=str(tmp_path / "budget.db"), limits={"tokens": 2}, window_s=3600)
    return reg, ledger, agent_reg


def test_budget_exhaustion_stops_and_records_partial_state(harness_with_tiny_budget) -> None:
    reg, ledger, agent_reg = harness_with_tiny_budget
    h = WorkerHarness("hermes-1", registry=reg, budget_ledger=ledger, agent_registry=agent_reg)
    assert h.execute("work", context={"changed_files": ["src/a.py"], "estimated_tokens": 1}).ok is True
    assert h.execute("work", context={"changed_files": ["src/b.py"], "estimated_tokens": 1}).ok is True
    exhausted = h.execute("work", context={"changed_files": ["src/c.py"], "estimated_tokens": 1})
    assert exhausted.ok is False
    assert exhausted.event == "harness:budget_exhausted"
    assert exhausted.artifacts.get("partial") is True
    assert "tokens" in (exhausted.error or "").lower()
    # No silent downgrade: a cheaper category is not spent instead.
    assert ledger.state()["tokens"]["remaining"] == 0


def test_zero_cap_denies_everything(harness_with_tiny_budget) -> None:
    reg, ledger, agent_reg = harness_with_tiny_budget
    # Re-register with limit 0 → every spend is denied.
    reg.revoke("hermes-1", actor="wilson", approved_by="wilson")
    reg.register(HarnessSpec(harness_id="hermes-2", agent_id="agent.hermes", allowed_files=("src/**",), budget_category="tokens", budget_limit=0), actor="wilson", approved_by="wilson")
    zero_ledger = BudgetLedger(db_path=str(ledger.db_path), limits={"tokens": 0}, window_s=3600)
    h = WorkerHarness("hermes-2", registry=reg, budget_ledger=zero_ledger, agent_registry=agent_reg)
    result = h.execute("work", context={"changed_files": ["src/a.py"]})
    assert result.ok is False
    assert result.event == "harness:budget_exhausted"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `PYTHONPATH=src python -m pytest -q tests/harnesses/test_harness_budget.py -p no:cacheprovider`
Expected: budget gate not yet implemented → second test passes as `harness:completed` instead of `budget_exhausted`.

- [ ] **Step 3: Implement the budget gate in `worker.py`**

Add at the top of `WorkerHarness.execute()`, after the H1/H3 checks and before any worktree creation:

```python
category = spec.budget_category
estimated = int((context or {}).get("estimated_tokens", 1))
if estimated < 1:
    estimated = 1
if not self._ledger.spend(category, estimated):
    state = self._ledger.state().get(category, {"spent": 0, "limit": spec.budget_limit, "remaining": 0})
    return WorkerResult(ok=False, event="harness:budget_exhausted", harness_id=self._harness_id,
                        artifacts={"partial": True, "spent": state["spent"], "limit": state["limit"]},
                        telemetry={"budget_category": category, "budget_spent": state["spent"], "budget_limit": state["limit"], "budget_remaining": state["remaining"]},
                        error=f"budget exhausted: {category} limit {state['limit']} spent {state['spent']}")
```

Wire `self._ledger` from the constructor arg, defaulting to `BudgetLedger.from_settings()` when not injected (same pattern as `MissionStore`'s lazy chain).

- [ ] **Step 4: Run it to verify it passes**

Run: `PYTHONPATH=src python -m pytest -q tests/harnesses/test_harness_budget.py tests/harnesses/test_harness_worker.py -p no:cacheprovider`
Expected: `6 passed` (4 worker + 2 budget).

---

### Task 4: Parallel worktrees — H5

**Files:**
- Create: `src/msb_v3/harnesses/parallel.py` (or extend `worker.py` — prefer a small pure helper so the invariant is testable without a harness)
- Test: `tests/harnesses/test_harness_parallel.py`

**Interfaces:**
- Produces:
  - `disjoint_allowed_files(a: tuple[str, ...], b: tuple[str, ...]) -> bool` — `True` when no file could match both globs. Implemented by checking whether any `a` glob overlaps any `b` glob via `fnmatch` on a synthetic probe (the conservative rule: if either glob is `"**"` or the prefixes overlap, return `False` — only clearly disjoint prefixes like `("src/msb_v3/a/**",)` vs `("src/msb_v3/b/**",)` are `True`). The plan's `parallel` flag is authoritative: parallel worktrees are only issued when the caller passes `allow_parallel=True` **and** the file sets are disjoint.
  - `WorkerHarness` gains `worktree` lifecycle: on `execute()`, when `context.get("repo_path")` is set, the harness calls `create_worktree(repo_path)` to obtain an isolated temp worktree, does the bounded work there, then `compute_changes(repo_path, worktree)` for the diff, then removes the worktree. Each harness invocation gets its own temp directory — two concurrent `WorkerHarness` instances never share a worktree path (pinned by the test checking `worktree` telemetry or temp-dir uniqueness).

Rules pinned:
1. H5: parallel tasks get parallel worktrees **only** when the plan marks them file-disjoint. `disjoint_allowed_files` is the gate; when it returns `False`, the second dispatch is refused with `event="harness:blocked:parallel_overlap"`.
2. Per-worker worktree: two harnesses with disjoint `allowed_files` each receive a distinct worktree path; no shared mutable state.

- [ ] **Step 1: Write the failing test** at `tests/harnesses/test_harness_parallel.py`

```python
"""Harness parallel worktrees — H5 file-disjoint (Phase 2)."""

from pathlib import Path

from msb_ledger.audit_chain import AuditChain
from msb_v3.agent.identity import AgentIdentity, AgentRegistry
from msb_v3.governance.budget import BudgetLedger
from msb_v3.harnesses.parallel import disjoint_allowed_files
from msb_v3.harnesses.registry import HarnessRegistry, HarnessSpec
from msb_v3.harnesses.worker import WorkerHarness


def test_disjoint_check() -> None:
    assert disjoint_allowed_files(("src/msb_v3/a/**",), ("src/msb_v3/b/**",)) is True
    assert disjoint_allowed_files(("src/msb_v3/a/**",), ("src/msb_v3/a/**",)) is False
    assert disjoint_allowed_files(("src/**",), ("tests/**",)) is False  # "**" overlaps
    assert disjoint_allowed_files(("src/msb_v3/harnesses/**",), ("src/msb_v3/harnesses/worker.py",)) is False


def test_parallel_worktrees_are_distinct_when_disjoint(tmp_path: Path) -> None:
    agent_reg = AgentRegistry(str(tmp_path / "agents.db"))
    for aid in ("agent.a", "agent.b"):
        agent_reg.register(AgentIdentity(agent_id=aid, name=aid, kind="local", provider_id="local.slice", granted_capabilities=("chat",)))
    chain = AuditChain(str(tmp_path / "audit.db"), allow_keyless=True)
    reg = HarnessRegistry(str(tmp_path / "h.db"), chain=chain, operators={"wilson"}, agent_registry=agent_reg)
    reg.register(HarnessSpec(harness_id="h-a", agent_id="agent.a", allowed_files=("src/msb_v3/a/**",), budget_category="tokens", budget_limit=100), actor="wilson", approved_by="wilson")
    reg.register(HarnessSpec(harness_id="h-b", agent_id="agent.b", allowed_files=("src/msb_v3/b/**",), budget_category="tokens", budget_limit=100), actor="wilson", approved_by="wilson")
    ledger = BudgetLedger(db_path=str(tmp_path / "budget.db"), limits={"tokens": 100}, window_s=3600)
    ha = WorkerHarness("h-a", registry=reg, budget_ledger=ledger, agent_registry=agent_reg)
    hb = WorkerHarness("h-b", registry=reg, budget_ledger=ledger, agent_registry=agent_reg)
    ra = ha.execute("work a", context={"changed_files": ["src/msb_v3/a/one.py"], "repo_path": str(tmp_path)})
    rb = hb.execute("work b", context={"changed_files": ["src/msb_v3/b/two.py"], "repo_path": str(tmp_path)})
    assert ra.ok and rb.ok
    # Worktrees are distinct temp dirs (or at least distinct telemetry worktree ids).
    assert ra.telemetry.get("worktree") != rb.telemetry.get("worktree") or ra.artifacts != rb.artifacts


def test_overlapping_parallel_is_blocked(tmp_path: Path) -> None:
    agent_reg = AgentRegistry(str(tmp_path / "agents.db"))
    agent_reg.register(AgentIdentity(agent_id="agent.a", name="a", kind="local", provider_id="local.slice", granted_capabilities=("chat",)))
    agent_reg.register(AgentIdentity(agent_id="agent.b", name="b", kind="local", provider_id="local.slice", granted_capabilities=("chat",)))
    chain = AuditChain(str(tmp_path / "audit.db"), allow_keyless=True)
    reg = HarnessRegistry(str(tmp_path / "h.db"), chain=chain, operators={"wilson"}, agent_registry=agent_reg)
    reg.register(HarnessSpec(harness_id="h-a", agent_id="agent.a", allowed_files=("src/msb_v3/a/**",), budget_category="tokens", budget_limit=100), actor="wilson", approved_by="wilson")
    reg.register(HarnessSpec(harness_id="h-b", agent_id="agent.b", allowed_files=("src/msb_v3/a/**",), budget_category="tokens", budget_limit=100), actor="wilson", approved_by="wilson")
    ledger = BudgetLedger(db_path=str(tmp_path / "budget.db"), limits={"tokens": 100}, window_s=3600)
    # Caller asserts parallel — the harness checks disjointness.
    from msb_v3.harnesses.parallel import disjoint_allowed_files as disjoint
    assert disjoint(("src/msb_v3/a/**",), ("src/msb_v3/a/**",)) is False
    ha = WorkerHarness("h-a", registry=reg, budget_ledger=ledger, agent_registry=agent_reg)
    hb = WorkerHarness("h-b", registry=reg, budget_ledger=ledger, agent_registry=agent_reg)
    # Second dispatch with allow_parallel=True but overlapping files → BLOCK.
    result = hb.execute("work b", context={"changed_files": ["src/msb_v3/a/two.py"], "allow_parallel": True, "other_allowed_files": ("src/msb_v3/a/**",)})
    assert result.ok is False
    assert result.event == "harness:blocked:parallel_overlap"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `PYTHONPATH=src python -m pytest -q tests/harnesses/test_harness_parallel.py -p no:cacheprovider`
Expected: `ModuleNotFoundError: No module named 'msb_v3.harnesses.parallel'`.

- [ ] **Step 3: Write the implementation** at `src/msb_v3/harnesses/parallel.py` + wire `WorkerHarness` parallel check

`parallel.py` is ~40 lines: `disjoint_allowed_files` compares every `a` glob to every `b` glob — if any pair could match the same path (check prefix overlap before the first `*`), return `False`; otherwise `True`. Conservative: `**` or `src/**` overlapping `src/msb_v3/a/**` is overlapping.

In `worker.py`, after the budget gate, when `context.get("allow_parallel")` and `context.get("other_allowed_files")` are set, call `disjoint_allowed_files(spec.allowed_files, tuple(other))` and BLOCK with `event="harness:blocked:parallel_overlap"` when `False`.

Per-worker worktree: when `context.get("repo_path")` is set, call `create_worktree(repo_path)` and include `telemetry["worktree"] = worktree` so the test can assert distinctness; clean up the temp dir on exit.

- [ ] **Step 4: Run it to verify it passes**

Run: `PYTHONPATH=src python -m pytest -q tests/harnesses/test_harness_parallel.py -p no:cacheprovider`
Expected: `3 passed`.

---

### Task 5: Exports, surface, re-measure, gates, patch

**Files:**
- Modify: `src/msb_v3/harnesses/__init__.py` — public exports
- Modify: `docs/SURFACE.md` — keep `msb_v3/harnesses` as OPTIONAL with Phase 2 justification
- Modify: `docs/what-msb-v3-is.md` — re-measured scale counts
- Test: `tests/docs/test_surface_map.py` must stay green

**Interfaces:**
- `src/msb_v3/harnesses/__init__.py` re-exports: `HarnessRegistry`, `HarnessSpec`, `HarnessRecord`, `HarnessError`, `HARNESS_COMPONENT`, `HARNESS_OPERATORS_ENV`, `operators_from_env` (registry); `WorkerHarness`, `WorkerResult`, `FileBoundaryError`, `MERGE_MARKERS`, `enforce_allowed_files` (worker); `disjoint_allowed_files` (parallel); plus existing `BaseHarness`, `HarnessResult`, `ChatHarness`, `chat_actor` (base).

- [ ] **Step 1: Wire the public exports**

```python
"""Harnesses — Phase 2: registry + worker base (blueprint §6)."""

from msb_v3.harnesses.base import BaseHarness, ChatHarness, HarnessResult, chat_actor
from msb_v3.harnesses.parallel import disjoint_allowed_files
from msb_v3.harnesses.registry import (
    HARNESS_COMPONENT,
    HARNESS_OPERATORS_ENV,
    HarnessError,
    HarnessRecord,
    HarnessRegistry,
    HarnessSpec,
    operators_from_env,
)
from msb_v3.harnesses.worker import FileBoundaryError, MERGE_MARKERS, WorkerHarness, WorkerResult, enforce_allowed_files

__all__ = [
    "BaseHarness", "ChatHarness", "HarnessResult", "chat_actor",
    "HARNESS_COMPONENT", "HARNESS_OPERATORS_ENV", "HarnessError", "HarnessRecord", "HarnessRegistry", "HarnessSpec", "operators_from_env",
    "FileBoundaryError", "MERGE_MARKERS", "WorkerHarness", "WorkerResult", "enforce_allowed_files",
    "disjoint_allowed_files",
]
```

- [ ] **Step 2: Update SURFACE.md**

The `msb_v3/harnesses` row stays OPTIONAL but its justification grows: "Harness registry + worker base (Phase 2 of the agent control plane, blueprint §6): H1–H5 pinned, per-worker worktree, budget stop records partial state. No HTTP surface; the store is the spine later phases build on." Keep the class OPTIONAL — no router is added in this phase.

- [ ] **Step 3: Re-measure `docs/what-msb-v3-is.md`**

Run the scale-count script (or `scripts/measure_scale.py` if present) from the worktree with `PYTHONPATH=src`, update the `404/79,682 src` counts and the `357 tests` count to the new totals. The records gate (`scripts/production_gate.py`) requires the counts to match HEAD.

- [ ] **Step 4: Run the gates**

Run in the worktree with `PYTHONPATH=src`:

```bash
PYTHONPATH=src python -m pytest -q tests/harnesses -p no:cacheprovider
PYTHONPATH=src python -m pytest -q tests/mission -p no:cacheprovider
PYTHONPATH=src python -m ruff check src/msb_v3/harnesses tests/harnesses
PYTHONPATH=src python -m mypy src/msb_v3/harnesses --no-error-summary | head -n 50
PYTHONPATH=src python -m pytest -q tests/docs/test_surface_map.py -p no:cacheprovider
```

Expected: all green, `mypy` clean, surface map green.

- [ ] **Step 5: Full regression (sampled)**

Run: `PYTHONPATH=src python -m pytest -q -p no:cacheprovider 2>&1 | tail -n 20`
Expected: no new failures vs `main` at `279359a`. If disk is still 99% full, pass `MSB_SKIP_PORTABILITY=1` to the portability gate.

- [ ] **Step 6: Produce the patch (no commit)**

From the worktree, with the work left uncommitted (D-3):

```bash
git diff --stat
git diff > ~/projects/AI-Agents/msb-v3/ai-workspace/job-board/in-progress/JOB-030-mission-harness-phase2/JOB-030.patch
# Or, if JOB-030 is queued as ready/:
git diff > /tmp/JOB-030.patch && ls -lh /tmp/JOB-030.patch
```

The patch must apply cleanly on `main` at `279359a`: `git -C ~/projects/AI-Agents/msb-v3 apply --check /tmp/JOB-030.patch` → exit 0.

---

## Verification Gates (Wilson)

Each phase ends at a gate Wilson signs. Phase 2's gate (blueprint §12):

> H1–H5 each pinned by a test; per-worker worktree; budget stop records partial state

Concrete checks:

- [ ] `tests/harnesses/test_harness_registry.py` pins H1 (identity mismatch + fingerprint drift) and the chain-then-commit discipline.
- [ ] `tests/harnesses/test_harness_worker.py` pins H2 (file-boundary BLOCK) and H3 (merge-authority BLOCK).
- [ ] `tests/harnesses/test_harness_budget.py` pins H4 (exhaustion stops, partial state recorded, no silent downgrade).
- [ ] `tests/harnesses/test_harness_parallel.py` pins H5 (file-disjoint gate + per-worker worktree distinctness).
- [ ] `ruff check` and `mypy src/msb_v3/harnesses` clean.
- [ ] `tests/docs/test_surface_map.py` green after SURFACE.md update.
- [ ] Patch applies on `main` at `279359a` with `git apply --check` → 0.
- [ ] No harness holds merge authority — grep `git merge|git push|gh pr merge` in `src/msb_v3/harnesses` returns only the `MERGE_MARKERS` constant and its test.

**Out of scope for Phase 2:** capability registry, router, cost policy (Phase 3); PM/Planner/Verifier harnesses (Phases 4–5, 7); Hermes/FreeBuff providers (Phase 6); mission gate (Phase 8); cockpit MEMORY layers (Phase 9); M1 run (Phase 10). Phases 1–3 are pure MSB-v3 code and need no external worker.
