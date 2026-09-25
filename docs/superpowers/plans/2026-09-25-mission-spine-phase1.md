# Mission Spine (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the versioned Mission object, its frozen state machine and its store, with every write evidenced on the audit chain. This is Phase 1 of `docs/blueprints/2026-09-25-agent-control-plane.md` (§4, §12).

**Architecture:** There's a new package, `src/msb_v3/mission/`, with three modules:
- `states.py`: the frozen transition matrix, as pure functions.
- `models.py`: frozen dataclasses, plus content hashing through the ledger v2 canonicalizer.
- `store.py`: SQLite projection. Every write runs in a transaction that commits only after the audit chain has accepted the matching event, so if the chain refuses, nothing is recorded.

Three kinds of version (blueprint → plan → task graph) each name their parent by hash, which makes staleness detectable. There's no HTTP surface and no workers yet; those come in later phases.

**Tech Stack:** Python 3.11+, sqlite3, `msb_ledger.audit_chain.AuditChain`, `msb_ledger.audit_v2` (canonicalize / sha256_hex), pytest, hypothesis.

## Global Constraints

- Work only in the worktree `~/projects/AI-Agents/msb-v3-mission`, on branch `feat/mission-spine`. Never touch `~/projects/AI-Agents/msb-v3` (main checkout).
- **Every python command is prefixed with `PYTHONPATH=src`.** The installed `msb_v3` package points at the main checkout. Without the prefix, tests silently import `main`'s code instead of this worktree's, so they "pass" while testing the wrong files.
- Python: `/opt/homebrew/Caskroom/miniforge/base/bin/python` (the `python` on PATH). There's no `.venv`.
- **No `git commit`, no `git push`, no `git merge`, no checkout of `main`.** `FREEBUFF.md` forbids commits outright, and blueprint D-3 says workers write in worktrees while only the gate and Wilson merge. **Skip every "Commit" step below.** They're there for an executor that's allowed to commit. The work stays in the worktree, and Task 5 Step 6 turns it into a patch.
- Don't add dependencies. `hypothesis` is already installed.
- The transition matrix is frozen. If a test disagrees with the matrix, stop and report. Don't edit `EXPECTED` or `_EDGES` to make it pass.
- Don't weaken a failing test. If a test fails after the implementation step, the implementation is wrong or the plan is. Record it in `notes/freebuff-observation.md` and stop.
- `ruff check src tests scripts` and `mypy src` must stay clean.

## File Structure

| File | Responsibility |
|---|---|
| `src/msb_v3/mission/states.py` | `MissionState`, the frozen matrix, human gates, gate verdicts |
| `src/msb_v3/mission/models.py` | `Mission`, `MissionVersion`, `StaleVersion`, `VersionKind`, `version_sha256` |
| `src/msb_v3/mission/store.py` | `MissionStore`: create / add_version / transition / reads; the atomic chain-then-commit write |
| `src/msb_v3/mission/__init__.py` | public exports |
| `tests/mission/test_mission_states.py` | the matrix snapshot and its rules |
| `tests/mission/test_mission_models.py` | hashing |
| `tests/mission/test_mission_store.py` | store behaviour, chain evidence, refusals |
| `tests/mission/test_mission_property.py` | random walks can never break the invariants |
| `docs/what-msb-v3-is.md` | re-measured scale counts (the records gate requires it) |

Test files carry a `test_mission_` prefix so their basenames can't collide with other `test_store.py` / `test_states.py` files in the suite.

---

### Task 0: Preflight

**Files:** none

- [ ] **Step 1: Confirm the worktree and branch**

Run: `cd ~/projects/AI-Agents/msb-v3-mission && git branch --show-current && git status --short`
Expected: `feat/mission-spine`, and apart from this plan file an empty status.

- [ ] **Step 2: Confirm imports resolve to this worktree**

Run: `PYTHONPATH=src python -c "import msb_v3, msb_ledger; print(msb_v3.__file__); print(msb_ledger.__file__)"`
Expected: both paths start with `/Users/lordwilson/projects/AI-Agents/msb-v3-mission/src/`. If either shows `/msb-v3/src/`, stop: every later result would be meaningless.

- [ ] **Step 3: Record the base**

Run: `git rev-parse HEAD`
Put the value in `result.json` as `base_head_seen`.

---

### Task 1: The frozen state machine

**Files:**
- Create: `src/msb_v3/mission/states.py`
- Create: `src/msb_v3/mission/__init__.py` (empty for now; exports come in Task 5)
- Test: `tests/mission/test_mission_states.py`

**Interfaces:**
- Produces:
  - `MissionState` (str Enum, 13 members)
  - `TERMINAL: frozenset[MissionState]`
  - `GATE_VERDICTS: frozenset[MissionState]`
  - `HUMAN_GATED: frozenset[tuple[MissionState, MissionState]]`
  - `MissionTransitionError(ValueError)`
  - `allowed_targets(state, resume_state=None) -> frozenset[MissionState]`
  - `validate_transition(from_state, to_state, *, resume_state=None) -> None` (raises `MissionTransitionError`)
  - `is_human_gated(from_state, to_state) -> bool`

- [ ] **Step 1: Create the empty package marker**

```bash
mkdir -p src/msb_v3/mission tests/mission
printf '"""Mission spine — Phase 1 of the agent control plane."""\n' > src/msb_v3/mission/__init__.py
```

- [ ] **Step 2: Write the failing test** at `tests/mission/test_mission_states.py`

```python
"""The frozen mission transition matrix (blueprint 2026-09-25 §4)."""

from __future__ import annotations

import pytest

from msb_v3.mission.states import (
    GATE_VERDICTS,
    TERMINAL,
    MissionTransitionError,
    allowed_targets,
    is_human_gated,
    validate_transition,
)
from msb_v3.mission.states import (
    MissionState as S,
)

# The whole matrix, pinned. Changing an edge means changing this snapshot in
# the same commit, on purpose. BLOCKED_ON_HUMAN is shown with resume BUILDING.
EXPECTED = {
    "DRAFT": ["ABANDONED", "BLOCKED_ON_HUMAN", "RESEARCHING"],
    "RESEARCHING": ["ABANDONED", "BLOCKED_ON_HUMAN", "BLUEPRINT_REVIEW"],
    "BLUEPRINT_REVIEW": ["ABANDONED", "BLOCKED_ON_HUMAN", "PLANNING", "RESEARCHING"],
    "PLANNING": ["ABANDONED", "BLOCKED_ON_HUMAN", "PLAN_REVIEW"],
    "PLAN_REVIEW": ["ABANDONED", "BLOCKED_ON_HUMAN", "BUILDING", "PLANNING"],
    "BUILDING": ["ABANDONED", "BLOCKED_ON_HUMAN", "VERIFYING"],
    "VERIFYING": [
        "ABANDONED", "BLOCKED_ON_HUMAN", "BUILDING",
        "GATED_BLOCK", "GATED_GREEN", "GATED_REVIEW",
    ],
    "GATED_GREEN": ["ABANDONED", "BLOCKED_ON_HUMAN", "MERGED"],
    "GATED_REVIEW": ["ABANDONED", "BLOCKED_ON_HUMAN", "BUILDING", "MERGED"],
    "GATED_BLOCK": ["ABANDONED", "BLOCKED_ON_HUMAN", "BUILDING"],
    "MERGED": [],
    "ABANDONED": [],
    "BLOCKED_ON_HUMAN": ["ABANDONED", "BUILDING"],
}


def test_matrix_is_frozen() -> None:
    snapshot = {
        s.value: sorted(
            t.value
            for t in allowed_targets(s, S.BUILDING if s is S.BLOCKED_ON_HUMAN else None)
        )
        for s in S
    }
    assert snapshot == EXPECTED


def test_happy_path_is_legal() -> None:
    path = [
        S.DRAFT, S.RESEARCHING, S.BLUEPRINT_REVIEW, S.PLANNING, S.PLAN_REVIEW,
        S.BUILDING, S.VERIFYING, S.GATED_GREEN, S.MERGED,
    ]
    for a, b in zip(path, path[1:]):
        validate_transition(a, b)


def test_block_verdict_can_never_merge() -> None:
    with pytest.raises(MissionTransitionError):
        validate_transition(S.GATED_BLOCK, S.MERGED)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        (S.RESEARCHING, S.PLANNING),
        (S.PLANNING, S.BUILDING),
        (S.BUILDING, S.GATED_GREEN),
        (S.DRAFT, S.MERGED),
        (S.VERIFYING, S.MERGED),
    ],
)
def test_skipping_a_stage_is_illegal(a: S, b: S) -> None:
    with pytest.raises(MissionTransitionError):
        validate_transition(a, b)


@pytest.mark.parametrize("terminal", sorted(TERMINAL, key=lambda s: s.value))
def test_terminal_states_have_no_exits(terminal: S) -> None:
    assert allowed_targets(terminal) == frozenset()


def test_every_working_state_can_block_on_human_and_be_abandoned() -> None:
    for s in S:
        if s in TERMINAL or s is S.BLOCKED_ON_HUMAN:
            continue
        assert {S.BLOCKED_ON_HUMAN, S.ABANDONED} <= allowed_targets(s)


def test_blocked_on_human_returns_only_to_where_it_came_from() -> None:
    assert allowed_targets(S.BLOCKED_ON_HUMAN, S.BUILDING) == {S.BUILDING, S.ABANDONED}
    with pytest.raises(MissionTransitionError):
        validate_transition(S.BLOCKED_ON_HUMAN, S.VERIFYING, resume_state=S.BUILDING)


def test_blocked_on_human_without_resume_state_can_only_be_abandoned() -> None:
    assert allowed_targets(S.BLOCKED_ON_HUMAN) == {S.ABANDONED}


def test_human_gates() -> None:
    assert is_human_gated(S.BLUEPRINT_REVIEW, S.PLANNING)
    assert is_human_gated(S.PLAN_REVIEW, S.BUILDING)
    assert is_human_gated(S.GATED_GREEN, S.MERGED)
    assert is_human_gated(S.GATED_REVIEW, S.MERGED)
    assert is_human_gated(S.BUILDING, S.ABANDONED)
    assert is_human_gated(S.BLOCKED_ON_HUMAN, S.BUILDING)
    assert not is_human_gated(S.DRAFT, S.RESEARCHING)
    assert not is_human_gated(S.BUILDING, S.BLOCKED_ON_HUMAN)
    assert not is_human_gated(S.VERIFYING, S.GATED_GREEN)


def test_gate_verdicts() -> None:
    assert GATE_VERDICTS == {S.GATED_GREEN, S.GATED_REVIEW, S.GATED_BLOCK}
```

- [ ] **Step 3: Run it to verify it fails**

Run: `PYTHONPATH=src python -m pytest -q tests/mission/test_mission_states.py -p no:cacheprovider`
Expected: collection error, `ModuleNotFoundError: No module named 'msb_v3.mission.states'`.

- [ ] **Step 4: Write the implementation** at `src/msb_v3/mission/states.py`

```python
"""Mission state machine — the frozen transition matrix.

Blueprint: docs/blueprints/2026-09-25-agent-control-plane.md §4.

The matrix is frozen the same way the memory-verify matrix is: changing an
edge means changing the snapshot pinned in
``tests/mission/test_mission_states.py`` in the same commit, on purpose.

Three rules live here and nowhere else:

* ``MERGED`` and ``ABANDONED`` are terminal — no exits.
* ``GATED_BLOCK`` never reaches ``MERGED``; a blocked mission goes back to
  ``BUILDING`` or is abandoned.
* ``BLOCKED_ON_HUMAN`` returns only to the state it came from (its
  ``resume_state``), or to ``ABANDONED``.
"""

from __future__ import annotations

from enum import Enum


class MissionState(str, Enum):
    DRAFT = "DRAFT"
    RESEARCHING = "RESEARCHING"
    BLUEPRINT_REVIEW = "BLUEPRINT_REVIEW"
    PLANNING = "PLANNING"
    PLAN_REVIEW = "PLAN_REVIEW"
    BUILDING = "BUILDING"
    VERIFYING = "VERIFYING"
    GATED_GREEN = "GATED_GREEN"
    GATED_REVIEW = "GATED_REVIEW"
    GATED_BLOCK = "GATED_BLOCK"
    MERGED = "MERGED"
    ABANDONED = "ABANDONED"
    BLOCKED_ON_HUMAN = "BLOCKED_ON_HUMAN"


S = MissionState

TERMINAL = frozenset({S.MERGED, S.ABANDONED})

# The verdicts only the MSB-v3 gate may issue (blueprint §9).
GATE_VERDICTS = frozenset({S.GATED_GREEN, S.GATED_REVIEW, S.GATED_BLOCK})

# Forward and rework edges. Every non-terminal state except BLOCKED_ON_HUMAN
# additionally reaches ABANDONED and BLOCKED_ON_HUMAN (see allowed_targets).
_EDGES: dict[MissionState, frozenset[MissionState]] = {
    S.DRAFT: frozenset({S.RESEARCHING}),
    S.RESEARCHING: frozenset({S.BLUEPRINT_REVIEW}),
    S.BLUEPRINT_REVIEW: frozenset({S.PLANNING, S.RESEARCHING}),
    S.PLANNING: frozenset({S.PLAN_REVIEW}),
    S.PLAN_REVIEW: frozenset({S.BUILDING, S.PLANNING}),
    S.BUILDING: frozenset({S.VERIFYING}),
    S.VERIFYING: frozenset({S.GATED_GREEN, S.GATED_REVIEW, S.GATED_BLOCK, S.BUILDING}),
    S.GATED_GREEN: frozenset({S.MERGED}),
    S.GATED_REVIEW: frozenset({S.MERGED, S.BUILDING}),
    S.GATED_BLOCK: frozenset({S.BUILDING}),
}

# Transitions that cross the authority boundary (blueprint §4). In addition,
# every move to ABANDONED and every return from BLOCKED_ON_HUMAN is a human
# decision (see is_human_gated).
HUMAN_GATED = frozenset({
    (S.BLUEPRINT_REVIEW, S.PLANNING),
    (S.PLAN_REVIEW, S.BUILDING),
    (S.GATED_GREEN, S.MERGED),
    (S.GATED_REVIEW, S.MERGED),
})


class MissionTransitionError(ValueError):
    """The requested move is not an edge of the frozen matrix."""


def allowed_targets(
    state: MissionState, resume_state: MissionState | None = None
) -> frozenset[MissionState]:
    """Every state ``state`` may move to. ``resume_state`` matters only for
    BLOCKED_ON_HUMAN, which returns to where it came from."""
    if state in TERMINAL:
        return frozenset()
    if state is S.BLOCKED_ON_HUMAN:
        if resume_state is None:
            return frozenset({S.ABANDONED})
        return frozenset({S.ABANDONED, resume_state})
    return _EDGES[state] | {S.ABANDONED, S.BLOCKED_ON_HUMAN}


def validate_transition(
    from_state: MissionState,
    to_state: MissionState,
    *,
    resume_state: MissionState | None = None,
) -> None:
    if to_state not in allowed_targets(from_state, resume_state):
        raise MissionTransitionError(
            f"illegal mission transition {from_state.value} -> {to_state.value}"
        )


def is_human_gated(from_state: MissionState, to_state: MissionState) -> bool:
    if to_state is S.ABANDONED:
        return True
    if from_state is S.BLOCKED_ON_HUMAN:
        return True
    return (from_state, to_state) in HUMAN_GATED
```

- [ ] **Step 5: Run it to verify it passes**

Run: `PYTHONPATH=src python -m pytest -q tests/mission/test_mission_states.py -p no:cacheprovider`
Expected: `15 passed`.

- [ ] **Step 6: Commit**

```bash
git add src/msb_v3/mission/__init__.py src/msb_v3/mission/states.py tests/mission/test_mission_states.py
git commit -m "feat(mission): frozen mission state machine (Phase 1)"
```

---

### Task 2: Records and content hashing

**Files:**
- Create: `src/msb_v3/mission/models.py`
- Test: `tests/mission/test_mission_models.py`

**Interfaces:**
- Consumes: `MissionState` from Task 1.
- Produces:
  - `VersionKind` (str Enum: `BLUEPRINT="blueprint"`, `PLAN="plan"`, `TASK_GRAPH="task_graph"`)
  - `PARENT_KIND: dict[VersionKind, VersionKind | None]`
  - `version_sha256(kind, parent_sha256: str | None, body: dict) -> str` (64 hex chars; raises `msb_ledger.audit_v2.CanonicalizationError` for NaN / inf / non-string keys)
  - frozen dataclasses:
    - `Mission(mission_id, objective, created_by, created_at, updated_at, state, resume_state, constraints)` with `.as_dict()`
    - `MissionVersion(mission_id, kind, version, sha256, parent_sha256, created_by, created_at, body)`
    - `StaleVersion(kind, version, reason)`

- [ ] **Step 1: Write the failing test** at `tests/mission/test_mission_models.py`

```python
"""Mission version hashing (blueprint 2026-09-25 §4)."""

from __future__ import annotations

import pytest

from msb_ledger.audit_v2 import CanonicalizationError
from msb_v3.mission.models import PARENT_KIND, VersionKind, version_sha256


def test_hash_ignores_key_order() -> None:
    a = version_sha256(VersionKind.BLUEPRINT, None, {"goal": "todo", "scope": ["board"]})
    b = version_sha256(VersionKind.BLUEPRINT, None, {"scope": ["board"], "goal": "todo"})
    assert a == b
    assert len(a) == 64


def test_same_body_against_a_different_parent_is_a_different_version() -> None:
    body = {"tasks": ["T-1"]}
    assert version_sha256(VersionKind.PLAN, "a" * 64, body) != version_sha256(
        VersionKind.PLAN, "b" * 64, body
    )


def test_same_body_as_a_different_kind_is_a_different_version() -> None:
    body = {"x": 1}
    assert version_sha256(VersionKind.PLAN, "a" * 64, body) != version_sha256(
        VersionKind.TASK_GRAPH, "a" * 64, body
    )


def test_non_canonical_body_is_refused() -> None:
    with pytest.raises(CanonicalizationError):
        version_sha256(VersionKind.BLUEPRINT, None, {"score": float("nan")})


def test_parent_chain() -> None:
    assert PARENT_KIND[VersionKind.BLUEPRINT] is None
    assert PARENT_KIND[VersionKind.PLAN] is VersionKind.BLUEPRINT
    assert PARENT_KIND[VersionKind.TASK_GRAPH] is VersionKind.PLAN
```

- [ ] **Step 2: Run it to verify it fails**

Run: `PYTHONPATH=src python -m pytest -q tests/mission/test_mission_models.py -p no:cacheprovider`
Expected: `ModuleNotFoundError: No module named 'msb_v3.mission.models'`.

- [ ] **Step 3: Write the implementation** at `src/msb_v3/mission/models.py`

```python
"""Mission records — plain frozen dataclasses.

Blueprint: docs/blueprints/2026-09-25-agent-control-plane.md §4.

A version is identified by its content hash. The parent's hash is part of
the preimage, so the same body built against a different parent is a
different version — that is what makes a stale plan detectable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from msb_ledger.audit_v2 import sha256_hex
from msb_v3.mission.states import MissionState


class VersionKind(str, Enum):
    BLUEPRINT = "blueprint"
    PLAN = "plan"
    TASK_GRAPH = "task_graph"


# Each kind is built against the latest version of its parent kind.
PARENT_KIND: dict[VersionKind, VersionKind | None] = {
    VersionKind.BLUEPRINT: None,
    VersionKind.PLAN: VersionKind.BLUEPRINT,
    VersionKind.TASK_GRAPH: VersionKind.PLAN,
}


def version_sha256(
    kind: VersionKind, parent_sha256: str | None, body: dict[str, Any]
) -> str:
    """Canonical (ledger v2) SHA-256 of kind + parent hash + body.

    Raises ``msb_ledger.audit_v2.CanonicalizationError`` for a body that is
    not canonical JSON (NaN, non-string keys, unsupported types)."""
    return sha256_hex({"kind": kind.value, "parent_sha256": parent_sha256, "body": body})


@dataclass(frozen=True)
class Mission:
    mission_id: str
    objective: str
    created_by: str
    created_at: str
    updated_at: str
    state: MissionState
    resume_state: MissionState | None
    constraints: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "objective": self.objective,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "state": self.state.value,
            "resume_state": self.resume_state.value if self.resume_state else None,
            "constraints": dict(self.constraints),
        }


@dataclass(frozen=True)
class MissionVersion:
    mission_id: str
    kind: VersionKind
    version: int
    sha256: str
    parent_sha256: str | None
    created_by: str
    created_at: str
    body: dict[str, Any]


@dataclass(frozen=True)
class StaleVersion:
    kind: VersionKind
    version: int
    reason: str
```

- [ ] **Step 4: Run it to verify it passes**

Run: `PYTHONPATH=src python -m pytest -q tests/mission/test_mission_models.py -p no:cacheprovider`
Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/msb_v3/mission/models.py tests/mission/test_mission_models.py
git commit -m "feat(mission): mission records and content-hashed versions"
```

---

### Task 3: The store

**Files:**
- Create: `src/msb_v3/mission/store.py`
- Test: `tests/mission/test_mission_store.py`

**Interfaces:**
- Consumes: everything in Tasks 1–2.
- Produces:
  - Constants: `OPERATORS_ENV = "MSB_MISSION_OPERATORS"`, `GATE_ACTOR = "msb-gate"`, `CHAIN_COMPONENT = "missions"`
  - `MissionError(RuntimeError)`
  - `operators_from_env() -> frozenset[str]`
  - `MissionStore(db_path: str | None = None, *, chain: AuditChainLike | None = None, operators: frozenset[str] | set[str] | None = None)` with methods:
    - `create(objective, *, created_by, constraints=None) -> Mission`
    - `add_version(mission_id, kind, body, *, created_by) -> MissionVersion`
    - `transition(mission_id, to_state, *, actor, reason, approved_by=None) -> Mission`
    - `get(mission_id) -> Mission`
    - `latest_version(mission_id, kind) -> MissionVersion | None`
    - `versions(mission_id, kind=None) -> list[MissionVersion]`
    - `stale_versions(mission_id) -> list[StaleVersion]`
    - `history(mission_id) -> list[dict]` (keys: `seq, from_state, to_state, actor, approved_by, reason, at`)
  - Chain events on component `"missions"`: `mission.created`, `mission.version` (hash, never the body), `mission.transition`.

Rules the store enforces, each pinned by a test below:
1. Every write commits only after the chain accepted its event. If the chain refuses, nothing is recorded (`MissionError`, "audit chain refused").
2. Only `GATE_ACTOR` may move a mission into `GATED_GREEN` / `GATED_REVIEW` / `GATED_BLOCK`.
3. Human-gated moves need `approved_by` in the configured operators. No operators configured means refused (fail closed).
4. `BLUEPRINT_REVIEW` needs a blueprint version. `PLAN_REVIEW` and `BUILDING` need a plan that isn't stale.
5. Versions and transitions are append-only. SQLite triggers refuse UPDATE and DELETE.
6. Re-adding the latest version's exact content against the same parent is a no-op: no new version, no chain event.
7. Staleness propagates: blueprint v2 makes the plan stale, and the task graph over that plan is stale too.

- [ ] **Step 1: Write the failing test** at `tests/mission/test_mission_store.py`

```python
"""Mission store: creation, versions, staleness, transitions, chain evidence
(blueprint 2026-09-25 §4, Phase 1)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from msb_ledger.audit_chain import AuditChain
from msb_v3.mission.models import VersionKind
from msb_v3.mission.states import MissionState as S
from msb_v3.mission.states import MissionTransitionError
from msb_v3.mission.store import (
    GATE_ACTOR,
    OPERATORS_ENV,
    MissionError,
    MissionStore,
    operators_from_env,
)


class RefusingChain:
    """An audit chain that is down."""

    def append(self, component: str, event_type: str, payload: dict[str, Any]) -> None:
        raise RuntimeError("chain unavailable")


@pytest.fixture
def chain(tmp_path: Path) -> AuditChain:
    return AuditChain(str(tmp_path / "audit.db"), allow_keyless=True)


@pytest.fixture
def store(tmp_path: Path, chain: AuditChain) -> MissionStore:
    return MissionStore(str(tmp_path / "missions.db"), chain=chain, operators={"wilson"})


def events(chain: AuditChain, event_type: str) -> list[Any]:
    return [r for r in chain.get_chain("missions") if r.event_type == event_type]


def to_plan_review(store: MissionStore, mid: str) -> None:
    store.transition(mid, S.RESEARCHING, actor="claude-pm", reason="start research")
    store.add_version(mid, VersionKind.BLUEPRINT, {"goal": "todo board"}, created_by="claude-pm")
    store.transition(mid, S.BLUEPRINT_REVIEW, actor="claude-pm", reason="blueprint ready")
    store.transition(mid, S.PLANNING, actor="wilson", reason="ok", approved_by="wilson")
    store.add_version(mid, VersionKind.PLAN, {"tasks": ["T-1"]}, created_by="planner")
    store.transition(mid, S.PLAN_REVIEW, actor="planner", reason="plan ready")


def to_verifying(store: MissionStore, mid: str) -> None:
    to_plan_review(store, mid)
    store.transition(mid, S.BUILDING, actor="wilson", reason="ok", approved_by="wilson")
    store.transition(mid, S.VERIFYING, actor="hermes", reason="built")


# -- creation ---------------------------------------------------------------


def test_create_starts_in_draft_and_is_on_the_chain(
    store: MissionStore, chain: AuditChain
) -> None:
    m = store.create("Build a todo board", created_by="wilson", constraints={"budget_usd": 5})
    assert m.state is S.DRAFT
    assert m.mission_id.startswith("mission-")
    assert store.get(m.mission_id) == m
    created = events(chain, "mission.created")
    assert [e.payload["mission_id"] for e in created] == [m.mission_id]
    assert created[0].payload["constraints"] == {"budget_usd": 5}


@pytest.mark.parametrize(("objective", "who"), [("   ", "wilson"), ("todo", "  ")])
def test_create_requires_objective_and_author(
    store: MissionStore, objective: str, who: str
) -> None:
    with pytest.raises(MissionError):
        store.create(objective, created_by=who)


def test_non_canonical_constraints_are_refused(store: MissionStore) -> None:
    with pytest.raises(MissionError, match="canonical"):
        store.create("todo", created_by="wilson", constraints={"budget": float("inf")})


def test_unknown_mission(store: MissionStore) -> None:
    with pytest.raises(MissionError, match="unknown mission"):
        store.get("mission-nope")


def test_chain_refusal_records_nothing(tmp_path: Path) -> None:
    store = MissionStore(str(tmp_path / "m.db"), chain=RefusingChain(), operators={"wilson"})  # type: ignore[arg-type]
    with pytest.raises(MissionError, match="audit chain refused"):
        store.create("todo", created_by="wilson")
    conn = sqlite3.connect(tmp_path / "m.db")
    try:
        assert conn.execute("SELECT COUNT(*) FROM missions").fetchone()[0] == 0
    finally:
        conn.close()


# -- versions and staleness -------------------------------------------------


def test_versions_are_numbered_and_hashed(store: MissionStore, chain: AuditChain) -> None:
    mid = store.create("todo", created_by="wilson").mission_id
    v1 = store.add_version(mid, VersionKind.BLUEPRINT, {"goal": "a"}, created_by="claude-pm")
    v2 = store.add_version(mid, VersionKind.BLUEPRINT, {"goal": "b"}, created_by="claude-pm")
    assert (v1.version, v2.version) == (1, 2)
    assert v1.sha256 != v2.sha256
    assert v1.parent_sha256 is None
    assert store.latest_version(mid, VersionKind.BLUEPRINT) == v2
    on_chain = events(chain, "mission.version")
    assert [e.payload["sha256"] for e in on_chain] == [v1.sha256, v2.sha256]
    assert "body" not in on_chain[0].payload


def test_plan_needs_a_blueprint_and_names_it(store: MissionStore) -> None:
    mid = store.create("todo", created_by="wilson").mission_id
    with pytest.raises(MissionError, match="none yet"):
        store.add_version(mid, VersionKind.PLAN, {"tasks": []}, created_by="planner")
    bp = store.add_version(mid, VersionKind.BLUEPRINT, {"goal": "a"}, created_by="claude-pm")
    plan = store.add_version(mid, VersionKind.PLAN, {"tasks": []}, created_by="planner")
    assert plan.parent_sha256 == bp.sha256


def test_re_adding_identical_content_is_a_no_op(store: MissionStore, chain: AuditChain) -> None:
    mid = store.create("todo", created_by="wilson").mission_id
    first = store.add_version(mid, VersionKind.BLUEPRINT, {"goal": "a"}, created_by="claude-pm")
    again = store.add_version(mid, VersionKind.BLUEPRINT, {"goal": "a"}, created_by="someone")
    assert again == first
    assert len(events(chain, "mission.version")) == 1


def test_staleness_propagates_down_the_chain(store: MissionStore) -> None:
    mid = store.create("todo", created_by="wilson").mission_id
    store.add_version(mid, VersionKind.BLUEPRINT, {"goal": "a"}, created_by="claude-pm")
    store.add_version(mid, VersionKind.PLAN, {"tasks": ["T-1"]}, created_by="planner")
    store.add_version(mid, VersionKind.TASK_GRAPH, {"nodes": ["T-1"]}, created_by="planner")
    assert store.stale_versions(mid) == []

    store.add_version(mid, VersionKind.BLUEPRINT, {"goal": "b"}, created_by="claude-pm")
    stale = {s.kind: s for s in store.stale_versions(mid)}
    assert set(stale) == {VersionKind.PLAN, VersionKind.TASK_GRAPH}
    assert "built against blueprint" in stale[VersionKind.PLAN].reason
    assert stale[VersionKind.TASK_GRAPH].reason == "its plan is stale"

    store.add_version(mid, VersionKind.PLAN, {"tasks": ["T-1"]}, created_by="planner")
    stale = {s.kind: s for s in store.stale_versions(mid)}
    assert set(stale) == {VersionKind.TASK_GRAPH}
    assert "built against plan" in stale[VersionKind.TASK_GRAPH].reason


def test_versions_and_transitions_are_append_only(tmp_path: Path, store: MissionStore) -> None:
    mid = store.create("todo", created_by="wilson").mission_id
    store.add_version(mid, VersionKind.BLUEPRINT, {"goal": "a"}, created_by="claude-pm")
    store.transition(mid, S.RESEARCHING, actor="claude-pm", reason="start")
    conn = sqlite3.connect(tmp_path / "missions.db")
    try:
        for sql in (
            "UPDATE mission_versions SET body = '{}'",
            "DELETE FROM mission_versions",
            "UPDATE mission_transitions SET reason = 'x'",
            "DELETE FROM mission_transitions",
        ):
            with pytest.raises(sqlite3.DatabaseError):
                conn.execute(sql)
    finally:
        conn.close()


# -- transitions ------------------------------------------------------------


def test_full_path_to_merged(store: MissionStore, chain: AuditChain) -> None:
    mid = store.create("todo", created_by="wilson").mission_id
    to_verifying(store, mid)
    store.transition(mid, S.GATED_GREEN, actor=GATE_ACTOR, reason="all checks pass")
    m = store.transition(mid, S.MERGED, actor="wilson", reason="ship", approved_by="wilson")
    assert m.state is S.MERGED
    history = store.history(mid)
    assert [h["to_state"] for h in history][-2:] == ["GATED_GREEN", "MERGED"]
    assert len(events(chain, "mission.transition")) == len(history)


def test_illegal_transition_changes_nothing(store: MissionStore, chain: AuditChain) -> None:
    mid = store.create("todo", created_by="wilson").mission_id
    with pytest.raises(MissionTransitionError):
        store.transition(mid, S.BUILDING, actor="hermes", reason="skip ahead")
    assert store.get(mid).state is S.DRAFT
    assert events(chain, "mission.transition") == []


@pytest.mark.parametrize("approver", [None, "mallory"])
def test_human_gate_needs_a_configured_operator(store: MissionStore, approver: str | None) -> None:
    mid = store.create("todo", created_by="wilson").mission_id
    to_plan_review(store, mid)
    with pytest.raises(MissionError, match="configured operator"):
        store.transition(mid, S.BUILDING, actor="hermes", reason="go", approved_by=approver)
    assert store.get(mid).state is S.PLAN_REVIEW


def test_no_operators_configured_means_every_gate_is_refused(
    tmp_path: Path, chain: AuditChain
) -> None:
    store = MissionStore(str(tmp_path / "m.db"), chain=chain, operators=set())
    mid = store.create("todo", created_by="wilson").mission_id
    with pytest.raises(MissionError, match="configured operator"):
        store.transition(mid, S.ABANDONED, actor="wilson", reason="stop", approved_by="wilson")


def test_operators_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OPERATORS_ENV, " wilson, ,ops-2 ")
    assert operators_from_env() == {"wilson", "ops-2"}
    monkeypatch.delenv(OPERATORS_ENV)
    assert operators_from_env() == frozenset()


def test_only_the_gate_issues_verdicts(store: MissionStore) -> None:
    mid = store.create("todo", created_by="wilson").mission_id
    to_verifying(store, mid)
    with pytest.raises(MissionError, match="gate verdicts"):
        store.transition(mid, S.GATED_GREEN, actor="claude-verify", reason="PASS")
    assert store.transition(mid, S.GATED_BLOCK, actor=GATE_ACTOR, reason="tests fail").state is S.GATED_BLOCK


def test_block_verdict_cannot_merge_even_with_approval(store: MissionStore) -> None:
    mid = store.create("todo", created_by="wilson").mission_id
    to_verifying(store, mid)
    store.transition(mid, S.GATED_BLOCK, actor=GATE_ACTOR, reason="tests fail")
    with pytest.raises(MissionTransitionError):
        store.transition(mid, S.MERGED, actor="wilson", reason="ship anyway", approved_by="wilson")


def test_blocked_on_human_resumes_where_it_left_off(store: MissionStore) -> None:
    mid = store.create("todo", created_by="wilson").mission_id
    to_plan_review(store, mid)
    store.transition(mid, S.BUILDING, actor="wilson", reason="ok", approved_by="wilson")
    blocked = store.transition(mid, S.BLOCKED_ON_HUMAN, actor="hermes", reason="need a key")
    assert blocked.resume_state is S.BUILDING
    with pytest.raises(MissionError, match="configured operator"):
        store.transition(mid, S.BUILDING, actor="hermes", reason="resume")
    with pytest.raises(MissionTransitionError):
        store.transition(mid, S.VERIFYING, actor="wilson", reason="jump", approved_by="wilson")
    resumed = store.transition(mid, S.BUILDING, actor="wilson", reason="key given", approved_by="wilson")
    assert (resumed.state, resumed.resume_state) == (S.BUILDING, None)


def test_blueprint_review_needs_a_blueprint(store: MissionStore) -> None:
    mid = store.create("todo", created_by="wilson").mission_id
    store.transition(mid, S.RESEARCHING, actor="claude-pm", reason="start")
    with pytest.raises(MissionError, match="needs a blueprint"):
        store.transition(mid, S.BLUEPRINT_REVIEW, actor="claude-pm", reason="nothing yet")


def test_plan_review_needs_a_plan(store: MissionStore) -> None:
    mid = store.create("todo", created_by="wilson").mission_id
    store.transition(mid, S.RESEARCHING, actor="claude-pm", reason="start")
    store.add_version(mid, VersionKind.BLUEPRINT, {"goal": "a"}, created_by="claude-pm")
    store.transition(mid, S.BLUEPRINT_REVIEW, actor="claude-pm", reason="ready")
    store.transition(mid, S.PLANNING, actor="wilson", reason="ok", approved_by="wilson")
    with pytest.raises(MissionError, match="needs a plan"):
        store.transition(mid, S.PLAN_REVIEW, actor="planner", reason="nothing yet")


def test_stale_plan_cannot_start_building(store: MissionStore) -> None:
    mid = store.create("todo", created_by="wilson").mission_id
    to_plan_review(store, mid)
    store.add_version(mid, VersionKind.BLUEPRINT, {"goal": "changed"}, created_by="claude-pm")
    with pytest.raises(MissionError, match="stale"):
        store.transition(mid, S.BUILDING, actor="wilson", reason="ok", approved_by="wilson")
    store.transition(mid, S.PLANNING, actor="planner", reason="replan")
    store.add_version(mid, VersionKind.PLAN, {"tasks": ["T-1", "T-2"]}, created_by="planner")
    store.transition(mid, S.PLAN_REVIEW, actor="planner", reason="plan v2")
    assert store.transition(mid, S.BUILDING, actor="wilson", reason="ok", approved_by="wilson").state is S.BUILDING


def test_terminal_mission_takes_nothing(store: MissionStore) -> None:
    mid = store.create("todo", created_by="wilson").mission_id
    store.transition(mid, S.ABANDONED, actor="wilson", reason="not needed", approved_by="wilson")
    with pytest.raises(MissionTransitionError):
        store.transition(mid, S.RESEARCHING, actor="wilson", reason="revive", approved_by="wilson")
    with pytest.raises(MissionError, match="no new versions"):
        store.add_version(mid, VersionKind.BLUEPRINT, {"goal": "a"}, created_by="claude-pm")


def test_chain_refusal_leaves_the_state_unchanged(tmp_path: Path, chain: AuditChain) -> None:
    db = str(tmp_path / "m.db")
    good = MissionStore(db, chain=chain, operators={"wilson"})
    mid = good.create("todo", created_by="wilson").mission_id
    down = MissionStore(db, chain=RefusingChain(), operators={"wilson"})  # type: ignore[arg-type]
    with pytest.raises(MissionError, match="audit chain refused"):
        down.transition(mid, S.RESEARCHING, actor="claude-pm", reason="start")
    assert good.get(mid).state is S.DRAFT
    assert good.history(mid) == []
```

- [ ] **Step 2: Run it to verify it fails**

Run: `PYTHONPATH=src python -m pytest -q tests/mission/test_mission_store.py -p no:cacheprovider`
Expected: `ModuleNotFoundError: No module named 'msb_v3.mission.store'`.

- [ ] **Step 3: Write the implementation** at `src/msb_v3/mission/store.py`

```python
"""Mission store — the versioned spine of the agent control plane.

Blueprint: docs/blueprints/2026-09-25-agent-control-plane.md §4 (Phase 1, §12).

A mission moves through the frozen matrix in ``msb_v3.mission.states``. Every
write — creation, transition, new version — runs inside one SQLite
transaction that commits only after the matching record is on the audit chain
(component ``"missions"``). If the chain refuses, nothing is recorded. This is
deliberately stricter than ``tasks.lifecycle``, which logs a chain failure and
carries on: a mission transition that cannot be evidenced does not happen.
One gap remains: if the chain append succeeds and the SQLite COMMIT then
fails, the chain holds an event the store does not. The chain is the
authoritative record, so that direction errs toward evidence.

What Phase 1 does NOT prove is actor identity. ``actor`` and ``approved_by``
are strings the caller supplies. Operator approval is checked against the
configured operator list (``MSB_MISSION_OPERATORS``) and gate verdicts
against ``GATE_ACTOR``, but nothing here authenticates the caller. That
arrives with the harness registry (Phase 2) and the operator-authenticated
API (Phase 9).
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

from msb_ledger.audit_chain import AuditChainLike
from msb_ledger.audit_v2 import CanonicalizationError, canonicalize
from msb_ledger.chain_anchor import anchored_chain_from_env
from msb_v3.core.config import settings
from msb_v3.mission.models import (
    PARENT_KIND,
    Mission,
    MissionVersion,
    StaleVersion,
    VersionKind,
    version_sha256,
)
from msb_v3.mission.states import (
    GATE_VERDICTS,
    TERMINAL,
    MissionState,
    is_human_gated,
    validate_transition,
)

# Same convention as tasks.lifecycle: projections live beside runtime/.
_DB = Path(settings.db_path).parent / "runtime" / "missions.db"

OPERATORS_ENV = "MSB_MISSION_OPERATORS"
GATE_ACTOR = "msb-gate"
CHAIN_COMPONENT = "missions"

# Artifacts a state requires before a mission may enter it.
_REQUIRES_BLUEPRINT = frozenset({MissionState.BLUEPRINT_REVIEW})
_REQUIRES_CURRENT_PLAN = frozenset({MissionState.PLAN_REVIEW, MissionState.BUILDING})

T = TypeVar("T")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS missions (
    mission_id   TEXT PRIMARY KEY,
    objective    TEXT NOT NULL,
    created_by   TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    state        TEXT NOT NULL,
    resume_state TEXT,
    constraints  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mission_transitions (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    mission_id  TEXT NOT NULL,
    from_state  TEXT NOT NULL,
    to_state    TEXT NOT NULL,
    actor       TEXT NOT NULL,
    approved_by TEXT,
    reason      TEXT NOT NULL,
    at          TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mission_versions (
    mission_id    TEXT NOT NULL,
    kind          TEXT NOT NULL,
    version       INTEGER NOT NULL,
    sha256        TEXT NOT NULL,
    parent_sha256 TEXT,
    created_by    TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    body          TEXT NOT NULL,
    PRIMARY KEY (mission_id, kind, version)
);
CREATE TRIGGER IF NOT EXISTS mission_versions_no_update
    BEFORE UPDATE ON mission_versions
    BEGIN SELECT RAISE(ABORT, 'mission versions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS mission_versions_no_delete
    BEFORE DELETE ON mission_versions
    BEGIN SELECT RAISE(ABORT, 'mission versions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS mission_transitions_no_update
    BEFORE UPDATE ON mission_transitions
    BEGIN SELECT RAISE(ABORT, 'mission transitions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS mission_transitions_no_delete
    BEFORE DELETE ON mission_transitions
    BEGIN SELECT RAISE(ABORT, 'mission transitions are append-only'); END;
"""


class MissionError(RuntimeError):
    """A mission operation was refused; nothing was recorded."""


def operators_from_env() -> frozenset[str]:
    """Operator ids allowed to approve human-gated transitions.

    Comma-separated ``MSB_MISSION_OPERATORS``. Unset or empty means no
    operator is configured, so every human-gated transition is refused
    (fail closed)."""
    raw = os.getenv(OPERATORS_ENV, "")
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: Any, what: str) -> str:
    try:
        return canonicalize(value)
    except CanonicalizationError as exc:
        raise MissionError(f"{what} is not canonical JSON: {exc}") from exc


def _init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(_SCHEMA)
    finally:
        conn.close()


class MissionStore:
    def __init__(
        self,
        db_path: str | None = None,
        *,
        chain: AuditChainLike | None = None,
        operators: frozenset[str] | set[str] | None = None,
    ) -> None:
        self.db_path = Path(db_path) if db_path else _DB
        self._chain = chain  # resolved lazily, like tasks.lifecycle
        self._operators = (
            operators_from_env() if operators is None else frozenset(operators)
        )
        _init_db(self.db_path)

    # -- internals ---------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        # isolation_level=None: we issue BEGIN IMMEDIATE / COMMIT ourselves.
        conn = sqlite3.connect(self.db_path, timeout=10.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    def _chain_or_default(self) -> AuditChainLike:
        if self._chain is None:
            self._chain = anchored_chain_from_env()
        return self._chain

    def _atomic(
        self,
        step: Callable[[sqlite3.Connection], tuple[str | None, dict[str, Any], T]],
    ) -> T:
        """Run ``step`` in one transaction; commit only once the chain holds
        its event. A step returning ``event_type=None`` wrote nothing and is
        rolled back without touching the chain."""
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            event_type, payload, result = step(conn)
            if event_type is None:
                conn.execute("ROLLBACK")
                return result
            try:
                self._chain_or_default().append(CHAIN_COMPONENT, event_type, payload)
            except Exception as exc:
                raise MissionError(
                    f"audit chain refused {event_type}; nothing was recorded ({exc})"
                ) from exc
            conn.execute("COMMIT")
            return result
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    @staticmethod
    def _row(conn: sqlite3.Connection, mission_id: str) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM missions WHERE mission_id = ?", (mission_id,)
        ).fetchone()
        if row is None:
            raise MissionError(f"unknown mission {mission_id!r}")
        return row

    @staticmethod
    def _mission(row: sqlite3.Row) -> Mission:
        return Mission(
            mission_id=row["mission_id"],
            objective=row["objective"],
            created_by=row["created_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            state=MissionState(row["state"]),
            resume_state=MissionState(row["resume_state"]) if row["resume_state"] else None,
            constraints=json.loads(row["constraints"]),
        )

    @staticmethod
    def _version(row: sqlite3.Row) -> MissionVersion:
        return MissionVersion(
            mission_id=row["mission_id"],
            kind=VersionKind(row["kind"]),
            version=row["version"],
            sha256=row["sha256"],
            parent_sha256=row["parent_sha256"],
            created_by=row["created_by"],
            created_at=row["created_at"],
            body=json.loads(row["body"]),
        )

    def _latest(
        self, conn: sqlite3.Connection, mission_id: str, kind: VersionKind
    ) -> MissionVersion | None:
        row = conn.execute(
            "SELECT * FROM mission_versions WHERE mission_id = ? AND kind = ? "
            "ORDER BY version DESC LIMIT 1",
            (mission_id, kind.value),
        ).fetchone()
        return self._version(row) if row is not None else None

    def _stale(self, conn: sqlite3.Connection, mission_id: str) -> list[StaleVersion]:
        latest = {kind: self._latest(conn, mission_id, kind) for kind in VersionKind}
        stale: list[StaleVersion] = []
        stale_kinds: set[VersionKind] = set()
        for kind in (VersionKind.PLAN, VersionKind.TASK_GRAPH):
            current = latest[kind]
            parent_kind = PARENT_KIND[kind]
            if current is None or parent_kind is None:
                continue
            parent = latest[parent_kind]
            if parent_kind in stale_kinds:
                reason = f"its {parent_kind.value} is stale"
            elif parent is not None and current.parent_sha256 != parent.sha256:
                reason = (
                    f"built against {parent_kind.value} {str(current.parent_sha256)[:12]}, "
                    f"current {parent_kind.value} is v{parent.version} {parent.sha256[:12]}"
                )
            else:
                continue
            stale.append(StaleVersion(kind=kind, version=current.version, reason=reason))
            stale_kinds.add(kind)
        return stale

    def _check_artifacts(
        self, conn: sqlite3.Connection, mission_id: str, target: MissionState
    ) -> None:
        if target in _REQUIRES_BLUEPRINT and self._latest(
            conn, mission_id, VersionKind.BLUEPRINT
        ) is None:
            raise MissionError(f"{target.value} needs a blueprint version first")
        if target in _REQUIRES_CURRENT_PLAN:
            if self._latest(conn, mission_id, VersionKind.PLAN) is None:
                raise MissionError(f"{target.value} needs a plan version first")
            stale = [s for s in self._stale(conn, mission_id) if s.kind is VersionKind.PLAN]
            if stale:
                raise MissionError(
                    f"{target.value} refused: plan v{stale[0].version} is stale "
                    f"({stale[0].reason})"
                )

    # -- writes ------------------------------------------------------------

    def create(
        self,
        objective: str,
        *,
        created_by: str,
        constraints: dict[str, Any] | None = None,
    ) -> Mission:
        objective = objective.strip()
        if not objective:
            raise MissionError("objective is required")
        if not created_by.strip():
            raise MissionError("created_by is required")
        constraints_json = _canonical(dict(constraints or {}), "constraints")
        now = _now()
        mission = Mission(
            mission_id=f"mission-{uuid.uuid4().hex[:12]}",
            objective=objective,
            created_by=created_by,
            created_at=now,
            updated_at=now,
            state=MissionState.DRAFT,
            resume_state=None,
            constraints=json.loads(constraints_json),
        )

        def step(conn: sqlite3.Connection) -> tuple[str | None, dict[str, Any], Mission]:
            conn.execute(
                "INSERT INTO missions (mission_id, objective, created_by, created_at, "
                "updated_at, state, resume_state, constraints) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL, ?)",
                (mission.mission_id, objective, created_by, now, now,
                 mission.state.value, constraints_json),
            )
            payload = {
                "mission_id": mission.mission_id,
                "objective": objective,
                "created_by": created_by,
                "constraints": mission.constraints,
            }
            return "mission.created", payload, mission

        return self._atomic(step)

    def add_version(
        self,
        mission_id: str,
        kind: VersionKind,
        body: dict[str, Any],
        *,
        created_by: str,
    ) -> MissionVersion:
        """Record a new immutable version of ``kind``, built against the
        latest version of its parent kind. Re-adding the latest version's
        exact content against the same parent is a no-op that returns it."""
        if not created_by.strip():
            raise MissionError("created_by is required")
        body_json = _canonical(body, f"{kind.value} body")

        def step(conn: sqlite3.Connection) -> tuple[str | None, dict[str, Any], MissionVersion]:
            state = MissionState(self._row(conn, mission_id)["state"])
            if state in TERMINAL:
                raise MissionError(
                    f"mission {mission_id} is {state.value}; it takes no new versions"
                )
            parent_kind = PARENT_KIND[kind]
            parent_sha: str | None = None
            if parent_kind is not None:
                parent = self._latest(conn, mission_id, parent_kind)
                if parent is None:
                    raise MissionError(
                        f"a {kind.value} is built against a {parent_kind.value}; "
                        "this mission has none yet"
                    )
                parent_sha = parent.sha256
            sha = version_sha256(kind, parent_sha, body)
            latest = self._latest(conn, mission_id, kind)
            if latest is not None and latest.sha256 == sha:
                return None, {}, latest
            version = MissionVersion(
                mission_id=mission_id,
                kind=kind,
                version=1 if latest is None else latest.version + 1,
                sha256=sha,
                parent_sha256=parent_sha,
                created_by=created_by,
                created_at=_now(),
                body=json.loads(body_json),
            )
            conn.execute(
                "INSERT INTO mission_versions (mission_id, kind, version, sha256, "
                "parent_sha256, created_by, created_at, body) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (mission_id, kind.value, version.version, sha, parent_sha,
                 created_by, version.created_at, body_json),
            )
            # The chain carries the hash, not the body: the body lives in the
            # store, and the hash is what proves which body it was.
            payload = {
                "mission_id": mission_id,
                "kind": kind.value,
                "version": version.version,
                "sha256": sha,
                "parent_sha256": parent_sha,
                "created_by": created_by,
            }
            return "mission.version", payload, version

        return self._atomic(step)

    def transition(
        self,
        mission_id: str,
        to_state: MissionState | str,
        *,
        actor: str,
        reason: str,
        approved_by: str | None = None,
    ) -> Mission:
        target = MissionState(to_state)
        if not actor.strip():
            raise MissionError("actor is required")
        if not reason.strip():
            raise MissionError("reason is required")

        def step(conn: sqlite3.Connection) -> tuple[str | None, dict[str, Any], Mission]:
            row = self._row(conn, mission_id)
            current = MissionState(row["state"])
            resume = MissionState(row["resume_state"]) if row["resume_state"] else None
            validate_transition(current, target, resume_state=resume)
            if target in GATE_VERDICTS and actor != GATE_ACTOR:
                raise MissionError(
                    f"only {GATE_ACTOR!r} issues gate verdicts; "
                    f"{actor!r} cannot move a mission to {target.value}"
                )
            if is_human_gated(current, target) and (
                approved_by is None or approved_by not in self._operators
            ):
                raise MissionError(
                    f"{current.value} -> {target.value} needs approval from a "
                    f"configured operator ({OPERATORS_ENV}); got {approved_by!r}"
                )
            self._check_artifacts(conn, mission_id, target)
            new_resume = current if target is MissionState.BLOCKED_ON_HUMAN else None
            now = _now()
            conn.execute(
                "UPDATE missions SET state = ?, resume_state = ?, updated_at = ? "
                "WHERE mission_id = ?",
                (target.value, new_resume.value if new_resume else None, now, mission_id),
            )
            conn.execute(
                "INSERT INTO mission_transitions (mission_id, from_state, to_state, "
                "actor, approved_by, reason, at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (mission_id, current.value, target.value, actor, approved_by, reason, now),
            )
            payload = {
                "mission_id": mission_id,
                "from_state": current.value,
                "to_state": target.value,
                "actor": actor,
                "approved_by": approved_by,
                "reason": reason,
            }
            return "mission.transition", payload, self._mission(self._row(conn, mission_id))

        return self._atomic(step)

    # -- reads -------------------------------------------------------------

    def get(self, mission_id: str) -> Mission:
        conn = self._connect()
        try:
            return self._mission(self._row(conn, mission_id))
        finally:
            conn.close()

    def latest_version(self, mission_id: str, kind: VersionKind) -> MissionVersion | None:
        conn = self._connect()
        try:
            self._row(conn, mission_id)
            return self._latest(conn, mission_id, kind)
        finally:
            conn.close()

    def versions(
        self, mission_id: str, kind: VersionKind | None = None
    ) -> list[MissionVersion]:
        conn = self._connect()
        try:
            self._row(conn, mission_id)
            if kind is None:
                rows = conn.execute(
                    "SELECT * FROM mission_versions WHERE mission_id = ? "
                    "ORDER BY kind, version",
                    (mission_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM mission_versions WHERE mission_id = ? AND kind = ? "
                    "ORDER BY version",
                    (mission_id, kind.value),
                ).fetchall()
            return [self._version(r) for r in rows]
        finally:
            conn.close()

    def stale_versions(self, mission_id: str) -> list[StaleVersion]:
        """Latest plan / task graph built against something that is no
        longer current. Staleness propagates: a task graph over a stale plan
        is stale."""
        conn = self._connect()
        try:
            self._row(conn, mission_id)
            return self._stale(conn, mission_id)
        finally:
            conn.close()

    def history(self, mission_id: str) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            self._row(conn, mission_id)
            rows = conn.execute(
                "SELECT seq, from_state, to_state, actor, approved_by, reason, at "
                "FROM mission_transitions WHERE mission_id = ? ORDER BY seq",
                (mission_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
```

- [ ] **Step 4: Run it to verify it passes**

Run: `PYTHONPATH=src python -m pytest -q tests/mission/test_mission_store.py -p no:cacheprovider`
Expected: `25 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/msb_v3/mission/store.py tests/mission/test_mission_store.py
git commit -m "feat(mission): mission store — chain-evidenced writes, gates, staleness"
```

---

### Task 4: Property test, and proof it can fail

**Files:**
- Test: `tests/mission/test_mission_property.py`

**Interfaces:**
- Consumes: `MissionStore`, `GATE_ACTOR`, `MissionError` (Task 3); `allowed_targets`, `is_human_gated`, `GATE_VERDICTS`, `TERMINAL`, `MissionTransitionError` (Task 1).

- [ ] **Step 1: Write the test** at `tests/mission/test_mission_property.py`

```python
"""Random walks over the mission store never break its invariants.

Whatever sequence of transitions, actors, approvals and new versions is
thrown at a mission, the recorded history must satisfy the blueprint's rules
(blueprint 2026-09-25 §4, §9) and match the audit chain one-for-one.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from hypothesis import event, given, settings
from hypothesis import strategies as st

from msb_ledger.audit_chain import AuditChain
from msb_v3.mission.models import VersionKind
from msb_v3.mission.states import (
    GATE_VERDICTS,
    TERMINAL,
    MissionState,
    MissionTransitionError,
    allowed_targets,
    is_human_gated,
)
from msb_v3.mission.store import GATE_ACTOR, MissionError, MissionStore

ACTORS = ["claude-pm", "planner", "hermes", GATE_ACTOR, "wilson"]

# Uniformly random targets almost never get past BLUEPRINT_REVIEW, which would
# leave BUILDING and the gate verdicts untested. So three steps in four pick a
# legal forward move (never ABANDONED or BLOCKED_ON_HUMAN, which stall or end
# the walk; wild steps still reach both). The approver and the new versions
# stay random, so the store's own refusals (missing operator, missing or stale
# plan, wrong gate actor) still fire. Half the walks start from a mission
# already in VERIFYING, so the gate verdicts and MERGED are exercised on every
# run rather than by luck.
steps = st.lists(
    st.tuples(
        st.integers(min_value=0, max_value=3),            # 0 = wild step
        st.integers(min_value=0, max_value=5),            # which legal target
        st.sampled_from(list(MissionState)),              # wild target
        st.sampled_from(ACTORS),
        st.sampled_from(["wilson", "wilson", None, "mallory"]),
        st.sampled_from([None, None, VersionKind.BLUEPRINT, VersionKind.PLAN, VersionKind.TASK_GRAPH]),
    ),
    min_size=1,
    max_size=60,
)


def _advance_to_verifying(store: MissionStore, mid: str) -> None:
    S = MissionState
    store.transition(mid, S.RESEARCHING, actor="claude-pm", reason="start")
    store.add_version(mid, VersionKind.BLUEPRINT, {"goal": "walk"}, created_by="claude-pm")
    store.transition(mid, S.BLUEPRINT_REVIEW, actor="claude-pm", reason="ready")
    store.transition(mid, S.PLANNING, actor="wilson", reason="ok", approved_by="wilson")
    store.add_version(mid, VersionKind.PLAN, {"tasks": ["T-1"]}, created_by="planner")
    store.transition(mid, S.PLAN_REVIEW, actor="planner", reason="ready")
    store.transition(mid, S.BUILDING, actor="wilson", reason="ok", approved_by="wilson")
    store.transition(mid, S.VERIFYING, actor="hermes", reason="built")


_SIDE_EXITS = frozenset({MissionState.ABANDONED, MissionState.BLOCKED_ON_HUMAN})


def _pick_target(store: MissionStore, mid: str, mode: int, pick: int, wild: MissionState) -> MissionState:
    mission = store.get(mid)
    legal = sorted(
        (t for t in allowed_targets(mission.state, mission.resume_state) if t not in _SIDE_EXITS),
        key=lambda s: s.value,
    )
    if mode == 0 or not legal:
        return wild
    return legal[pick % len(legal)]


@settings(max_examples=200, deadline=None)
@given(st.booleans(), steps)
def test_random_walks_never_break_invariants(
    start_verifying: bool,
    walk: list[tuple[int, int, MissionState, str, str | None, VersionKind | None]],
) -> None:
    with tempfile.TemporaryDirectory() as d:
        chain = AuditChain(str(Path(d) / "audit.db"), allow_keyless=True)
        store = MissionStore(str(Path(d) / "m.db"), chain=chain, operators={"wilson"})
        mid = store.create("walk", created_by="wilson").mission_id
        if start_verifying:
            _advance_to_verifying(store, mid)

        for n, (mode, pick, wild, actor, approved_by, new_version) in enumerate(walk):
            if new_version is not None:
                try:
                    store.add_version(mid, new_version, {"n": n}, created_by=actor)
                except MissionError:
                    pass  # terminal mission, or no parent version yet
            target = _pick_target(store, mid, mode, pick, wild)
            if mode != 0 and target in GATE_VERDICTS:
                actor = GATE_ACTOR
            before = store.get(mid)
            try:
                store.transition(mid, target, actor=actor, reason=f"step {n}", approved_by=approved_by)
            except (MissionError, MissionTransitionError):
                assert store.get(mid) == before
            event(f"reached {store.get(mid).state.value}")

        history = store.history(mid)
        on_chain = [r for r in chain.get_chain("missions") if r.event_type == "mission.transition"]
        assert len(on_chain) == len(history)
        for row, record in zip(history, on_chain):
            a, b = MissionState(row["from_state"]), MissionState(row["to_state"])
            assert (record.payload["from_state"], record.payload["to_state"]) == (a.value, b.value)
            assert a not in TERMINAL
            assert (a, b) != (MissionState.GATED_BLOCK, MissionState.MERGED)
            if is_human_gated(a, b):
                assert row["approved_by"] == "wilson"
            if b in GATE_VERDICTS:
                assert row["actor"] == GATE_ACTOR
```

- [ ] **Step 2: Run it and check coverage**

Run: `PYTHONPATH=src python -m pytest -q tests/mission/test_mission_property.py -p no:cacheprovider --hypothesis-show-statistics`
Expected: `1 passed`, with `reached GATED_GREEN`, `reached GATED_REVIEW`, `reached GATED_BLOCK` and `reached MERGED` all listed. If any of the four is missing, report it; the walk isn't reaching the gate.

- [ ] **Step 3: Prove the test catches a broken gate-actor rule**

```bash
cp src/msb_v3/mission/store.py /tmp/store.py.bak
sed -i '' 's/if target in GATE_VERDICTS and actor != GATE_ACTOR:/if False:/' src/msb_v3/mission/store.py
PYTHONPATH=src python -m pytest -q tests/mission/test_mission_property.py -p no:cacheprovider
cp /tmp/store.py.bak src/msb_v3/mission/store.py
```
Expected: `1 failed`. Save the output to `notes/test-mutation-gate-actor.log`.

- [ ] **Step 4: Prove the test catches a broken operator check**

```bash
sed -i '' 's/approved_by is None or approved_by not in self._operators/approved_by is None/' src/msb_v3/mission/store.py
PYTHONPATH=src python -m pytest -q tests/mission/test_mission_property.py -p no:cacheprovider
cp /tmp/store.py.bak src/msb_v3/mission/store.py
```
Expected: `1 failed`. Save the output to `notes/test-mutation-operator.log`.

- [ ] **Step 5: Confirm the store is restored**

Run: `git diff --stat src/msb_v3/mission/store.py && PYTHONPATH=src python -m pytest -q tests/mission -p no:cacheprovider`
Expected: no diff on `store.py`; `46 passed`.

- [ ] **Step 6: Commit**

```bash
git add tests/mission/test_mission_property.py
git commit -m "test(mission): random walks never break mission invariants"
```

---

### Task 5: Exports, full gates, hand-off

**Files:**
- Modify: `src/msb_v3/mission/__init__.py`
- Modify: `docs/what-msb-v3-is.md` (the "Scale, measured" paragraph only)

- [ ] **Step 1: Write the exports** at `src/msb_v3/mission/__init__.py`

```python
"""Mission spine — Phase 1 of the agent control plane.

Blueprint: docs/blueprints/2026-09-25-agent-control-plane.md §4, §12.
"""

from msb_v3.mission.models import (
    PARENT_KIND,
    Mission,
    MissionVersion,
    StaleVersion,
    VersionKind,
    version_sha256,
)
from msb_v3.mission.states import (
    GATE_VERDICTS,
    HUMAN_GATED,
    TERMINAL,
    MissionState,
    MissionTransitionError,
    allowed_targets,
    is_human_gated,
    validate_transition,
)
from msb_v3.mission.store import (
    CHAIN_COMPONENT,
    GATE_ACTOR,
    OPERATORS_ENV,
    MissionError,
    MissionStore,
    operators_from_env,
)

__all__ = [
    "CHAIN_COMPONENT",
    "GATE_ACTOR",
    "GATE_VERDICTS",
    "HUMAN_GATED",
    "OPERATORS_ENV",
    "PARENT_KIND",
    "TERMINAL",
    "Mission",
    "MissionError",
    "MissionState",
    "MissionStore",
    "MissionTransitionError",
    "MissionVersion",
    "StaleVersion",
    "VersionKind",
    "allowed_targets",
    "is_human_gated",
    "operators_from_env",
    "validate_transition",
    "version_sha256",
]
```

- [ ] **Step 2: Lint and type-check**

Run: `PYTHONPATH=src python -m ruff check src tests scripts && PYTHONPATH=src python -m mypy src`
Expected: `All checks passed!` and `Success: no issues found in 404 source files`.

- [ ] **Step 3: Re-measure the scale counts**

Run: `PYTHONPATH=src python scripts/doc_records.py`
It will FAIL with lines like `docs/what-msb-v3-is.md: Python under src/ claims 400 (src_files), live is 404`. In `docs/what-msb-v3-is.md`, change the date in `## Scale, measured 2026-09-24` or `2026-09-25` to today, and replace each claimed number with the live number the gate printed. Keep the thousands separators (`78,918`). Also re-compute the "Test source is N% the size of product source" line as `round(100 * tests_lines / src_lines)`. Re-run until it prints `[records] PASS`.

- [ ] **Step 4: Full suite**

Run: `PYTHONPATH=src python -m pytest -q tests/ -p no:cacheprovider 2>&1 | tail -5`
Expected: 0 failed. The count should be roughly 3,875 + 46 passed. Save the output to `notes/test-full-suite.log`. If a test outside `tests/mission` fails, run that same test in the main checkout (`cd ~/projects/AI-Agents/msb-v3 && python -m pytest -q <nodeid>`), without editing anything there. If it fails there too, it's pre-existing; record it and don't fix it.

- [ ] **Step 5: Commit**

```bash
git add src/msb_v3/mission/__init__.py docs/what-msb-v3-is.md
git commit -m "feat(mission): export the mission package; re-measure scale counts"
```

- [ ] **Step 6: Produce the hand-off artifacts** (in the job folder, not the repo)

```bash
JOB=~/projects/AI-Agents/msb-v3/ai-workspace/job-board/in-progress/JOB-029-mission-spine-phase1
git add -N src/msb_v3/mission tests/mission
git diff HEAD > "$JOB/output/JOB-029.patch"
git diff HEAD --stat > "$JOB/output/diffstat.txt"
```
`git add -N` only marks the new files as intended, so the diff includes them. Nothing is committed.
Write `result.json` following the job board schema: `diff_ref: "output/JOB-029.patch"`, and `tests_run` for the mission suite, both mutation checks, ruff+mypy, doc_records and the full suite. Don't push. Don't merge.

---

## What Phase 1 deliberately does NOT do

- **Actor identity is not authenticated.** `actor` and `approved_by` are caller-supplied strings. That arrives with the harness registry (Phase 2) and the operator-authenticated API (Phase 9). The store docstring says so.
- No HTTP routes, no cockpit panel, no workers, no router. Later phases.
- Mission fields `tasks[]`, `workers[]`, `verification[]`, `costs` and `artifacts[]` from blueprint §4 are not stored yet. Each belongs to the phase that produces it.
- No JSONL artifact directory (`data/missions/<id>/`). Version bodies live in SQLite until artifacts exist (Phase 4).
- One known gap: if the chain append succeeds and the SQLite COMMIT then fails, the chain holds an event the store doesn't have. That direction errs toward evidence, and it's documented in the store docstring.
