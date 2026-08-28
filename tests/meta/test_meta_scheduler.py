"""Behaviour pins for msb_v3.meta.scheduler (META-1 slice, built by qwen3:8b).

The worker never saw this file — it is the checker's ground truth.
"""

import pytest

from msb_v3.meta.contracts import MetaTask, TaskState
from msb_v3.meta.scheduler import (
    blocked_reason,
    has_cycle,
    ready_tasks,
    topological_order,
)


def T(tid, deps=None, state=TaskState.BLOCKED):
    return MetaTask(task_id=tid, objective="", dependencies=list(deps or []), state=state)


# ---- T1: has_cycle -----------------------------------------------------------

def test_has_cycle_empty():
    assert has_cycle([]) is False


def test_has_cycle_linear_is_false():
    assert has_cycle([T("a"), T("b", ["a"]), T("c", ["b"])]) is False


def test_has_cycle_direct():
    assert has_cycle([T("a", ["b"]), T("b", ["a"])]) is True


def test_has_cycle_self_loop():
    assert has_cycle([T("a", ["a"])]) is True


def test_has_cycle_unknown_dep_ignored():
    assert has_cycle([T("a", ["ghost"]), T("b", ["a"])]) is False


def test_has_cycle_three_node_ring():
    assert has_cycle([T("a", ["c"]), T("b", ["a"]), T("c", ["b"])]) is True


# ---- T2: topological_order --------------------------------------------------

def test_topo_linear():
    assert topological_order([T("c", ["b"]), T("b", ["a"]), T("a")]) == ["a", "b", "c"]


def test_topo_tie_break_is_input_order():
    # a and b both free; c depends on both. input order a,b,c -> a,b,c
    assert topological_order([T("a"), T("b"), T("c", ["a", "b"])]) == ["a", "b", "c"]


def test_topo_diamond():
    tasks = [T("d", ["b", "c"]), T("b", ["a"]), T("c", ["a"]), T("a")]
    order = topological_order(tasks)
    assert order.index("a") < order.index("b") < order.index("d")
    assert order.index("a") < order.index("c") < order.index("d")
    assert set(order) == {"a", "b", "c", "d"}


def test_topo_unknown_dep_does_not_block():
    assert topological_order([T("a", ["ghost"]), T("b", ["a"])]) == ["a", "b"]


def test_topo_raises_on_cycle():
    with pytest.raises(ValueError):
        topological_order([T("a", ["b"]), T("b", ["a"])])


# ---- T3: ready_tasks ------------------------------------------------------------

def test_ready_no_deps_blocked():
    out = ready_tasks([T("a"), T("b")])
    assert [t.task_id for t in out] == ["a", "b"]


def test_ready_requires_deps_passed():
    tasks = [T("a", state=TaskState.PASSED), T("b", ["a"]), T("c", ["a", "b"])]
    assert [t.task_id for t in ready_tasks(tasks)] == ["b"]


def test_ready_skips_non_blocked():
    tasks = [T("a", state=TaskState.RUNNING), T("b", state=TaskState.PASSED), T("c")]
    assert [t.task_id for t in ready_tasks(tasks)] == ["c"]


def test_ready_unknown_dep_never_ready():
    assert ready_tasks([T("a", ["ghost"])]) == []


def test_ready_preserves_order():
    tasks = [T("z"), T("y"), T("x")]
    assert [t.task_id for t in ready_tasks(tasks)] == ["z", "y", "x"]


# ---- T4: blocked_reason ------------------------------------------------------

def test_reason_not_blocked():
    by = {}
    assert blocked_reason(T("a", state=TaskState.RUNNING), by) == "state is RUNNING, not BLOCKED"


def test_reason_waiting_on_deps():
    a = T("a", state=TaskState.PASSED)
    b = T("b", state=TaskState.BLOCKED)
    task = T("c", ["a", "b"])
    by = {"a": a, "b": b}
    assert blocked_reason(task, by) == "waiting on: b"


def test_reason_waiting_includes_unknown():
    a = T("a", state=TaskState.PASSED)
    task = T("c", ["a", "ghost"])
    by = {"a": a}
    assert blocked_reason(task, by) == "waiting on: ghost"


def test_reason_ready():
    a = T("a", state=TaskState.PASSED)
    task = T("b", ["a"])
    assert blocked_reason(task, {"a": a}) == "ready"


def test_reason_ready_no_deps():
    assert blocked_reason(T("a"), {}) == "ready"
