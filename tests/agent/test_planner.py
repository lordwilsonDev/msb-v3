"""Tests for the agent planner + task DAG (msb_v3.agent.planner, .dag)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from msb_v3.agent.dag import Task, TaskGraph  # noqa: E402
from msb_v3.agent.intent import Intent  # noqa: E402
from msb_v3.agent.planner import plan, template_dag  # noqa: E402


class _Resp:
    def __init__(self, text: str) -> None:
        self.text = text
        self.model = "fake"
        self.latency_s = 0.0
        self.tool_calls = []


class _FakeClient:
    def __init__(self, text: str) -> None:
        self._text = text

    def generate(self, prompt, *, system=None, tools=None, temperature=0.2, max_tokens=2048):
        return _Resp(self._text)


class _BrokenClient:
    def generate(self, *args, **kwargs):
        raise ConnectionError("ollama down")


def _write_intent() -> Intent:
    return Intent(
        request="research the vault and write a brief",
        goals=("research the vault",),
        permissions=("read_vault", "write_file"),
        source="llm",
    )


def _read_intent() -> Intent:
    return Intent(request="what do we know about x", goals=("answer x",), source="llm")


# ---------------------------------------------------------------------------
# Template DAG
# ---------------------------------------------------------------------------

def test_template_with_write_permission_is_a_chain() -> None:
    graph = template_dag(_write_intent())
    assert graph.source == "template"
    ids = [t.task_id for t in graph.tasks]
    assert ids == ["research", "synthesize", "write"]
    assert graph.order() == list(graph.tasks)  # topological = parent order
    assert graph.by_id("research").verification_method == "search_returned_hits"
    assert graph.by_id("synthesize").verification_method == "synthesis_nonempty"
    assert graph.by_id("write").verification_method == "file_written_with_heading"
    assert graph.by_id("write").parent_id == "synthesize"
    assert graph.by_id("synthesize").parent_id == "research"


def test_template_without_write_permission_is_read_only() -> None:
    graph = template_dag(_read_intent())
    ids = [t.task_id for t in graph.tasks]
    assert ids == ["research", "synthesize"]
    assert all("write_file" not in t.permissions for t in graph.tasks)


def test_template_is_deterministic() -> None:
    a = template_dag(_write_intent())
    b = template_dag(_write_intent())
    assert [t.as_dict() for t in a.tasks] == [t.as_dict() for t in b.tasks]


# ---------------------------------------------------------------------------
# TaskGraph mechanics
# ---------------------------------------------------------------------------

def test_order_parents_before_children_with_branching() -> None:
    graph = TaskGraph(
        goal="g",
        tasks=(
            Task(task_id="a", goal="ga"),
            Task(task_id="b", goal="gb", parent_id="a"),
            Task(task_id="c", goal="gc", parent_id="a"),
            Task(task_id="d", goal="gd", parent_id="b"),
            Task(task_id="e", goal="ge", parent_id="b"),
        ),
    )
    order = [t.task_id for t in graph.order()]
    assert order.index("a") < order.index("b") < order.index("d")
    assert order.index("a") < order.index("c")
    assert graph.children_of("a")[0].task_id == "b"  # deterministic order
    assert graph.roots()[0].task_id == "a"


def test_order_detects_cycle() -> None:
    graph = TaskGraph(
        goal="g",
        tasks=(
            Task(task_id="a", goal="ga", parent_id="b"),
            Task(task_id="b", goal="gb", parent_id="a"),
        ),
    )
    assert graph.is_acyclic() is False
    with pytest.raises(ValueError):
        graph.order()


# ---------------------------------------------------------------------------
# LLM planner path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_plan_llm_path_parses_valid_tasks() -> None:
    client = _FakeClient(
        '{"tasks": ['
        '{"task_id": "research", "goal": "search the vault", "parent_id": null, '
        '"capabilities": ["read_vault"], "tools": ["search_query"], '
        '"expected_output": "sources", "verification_method": "search_returned_hits", '
        '"timeout_s": 90, "retry_policy": "retry:3"}, '
        '{"task_id": "write", "goal": "write brief", "parent_id": "research", '
        '"capabilities": ["write_file"], "tools": ["vault_write"], '
        '"expected_output": "file", "verification_method": "file_written", '
        '"timeout_s": 30, "retry_policy": "retry:1"}]}'
    )
    graph = await plan(_read_intent(), client=client)
    assert graph.source == "llm"
    assert [t.task_id for t in graph.tasks] == ["research", "write"]
    assert graph.by_id("research").timeout_s == 90.0
    assert graph.by_id("write").parent_id == "research"
    assert graph.order()[0].task_id == "research"


@pytest.mark.asyncio
async def test_llm_path_derives_inputs_from_parent_id() -> None:
    """Regression: an LLM DAG with parent_id must wire inputs so the
    executor passes parent outputs downstream (found live — the demo run
    wrote an empty brief because downstream tasks received no inputs)."""
    client = _FakeClient(
        '{"tasks": ['
        '{"task_id": "research", "goal": "search the vault", "parent_id": null, '
        '"capabilities": ["read_vault"], "tools": ["search_query"], '
        '"expected_output": "sources", "verification_method": "search_returned_hits", '
        '"timeout_s": 90, "retry_policy": "retry:3"}, '
        '{"task_id": "synthesize", "goal": "write brief", "parent_id": "research", '
        '"capabilities": ["llm_synthesis"], "tools": ["chat"], '
        '"expected_output": "brief", "verification_method": "synthesis_nonempty", '
        '"timeout_s": 60, "retry_policy": "retry:1"}, '
        '{"task_id": "write", "goal": "write brief to file", "parent_id": "synthesize", '
        '"capabilities": ["write_file"], "tools": ["vault_write"], '
        '"expected_output": "file", "verification_method": "file_written", '
        '"timeout_s": 30, "retry_policy": "retry:1"}]}'
    )
    graph = await plan(_write_intent(), client=client)
    assert graph.source == "llm"
    # roots declare no inputs; every child derives (from: parent) wiring
    assert graph.by_id("research").inputs == ()
    assert graph.by_id("synthesize").inputs == ({"from": "research", "kind": "output"},)
    assert graph.by_id("write").inputs == ({"from": "synthesize", "kind": "output"},)


@pytest.mark.asyncio
async def test_llm_path_floors_synthesis_timeout_at_120s() -> None:
    """Regression: the LLM planner must not hand the executor a sub-120s
    timeout for model generation (found live — qwen3 chose 30s for a real
    synthesis over actual vault sources and the executor timed out
    mid-generation). Non-chat tasks keep their model-chosen values."""
    client = _FakeClient(
        '{"tasks": ['
        '{"task_id": "research", "goal": "search", "parent_id": null, '
        '"capabilities": ["read_vault"], "tools": ["search_query"], '
        '"verification_method": "search_returned_hits", '
        '"timeout_s": 20, "retry_policy": "retry:2"}, '
        '{"task_id": "synthesize", "goal": "write brief", "parent_id": "research", '
        '"capabilities": ["llm_synthesis"], "tools": ["chat"], '
        '"verification_method": "synthesis_nonempty", '
        '"timeout_s": 30, "retry_policy": "retry:1"}]}'
    )
    graph = await plan(_read_intent(), client=client)
    assert graph.source == "llm"
    assert graph.by_id("research").timeout_s == 20.0  # untouched (no chat tool)
    assert graph.by_id("synthesize").timeout_s == 120.0  # floored


@pytest.mark.asyncio
async def test_plan_falls_back_on_garbage() -> None:
    graph = await plan(_read_intent(), client=_FakeClient("sorry, no plan for you"))
    assert graph.source == "template"
    assert graph.tasks[0].task_id == "research"


@pytest.mark.asyncio
async def test_plan_falls_back_on_unreachable_model() -> None:
    graph = await plan(_write_intent(), client=_BrokenClient())
    assert graph.source == "template"
    assert graph.tasks[-1].task_id == "write"  # permissions still honored


@pytest.mark.asyncio
async def test_plan_falls_back_on_cycle() -> None:
    client = _FakeClient(
        '{"tasks": ['
        '{"task_id": "a", "goal": "ga", "parent_id": "b", "capabilities": [], "tools": [], '
        '"verification_method": "none", "timeout_s": 60, "retry_policy": "retry:2"}, '
        '{"task_id": "b", "goal": "gb", "parent_id": "a", "capabilities": [], "tools": [], '
        '"verification_method": "none", "timeout_s": 60, "retry_policy": "retry:2"}]}'
    )
    graph = await plan(_read_intent(), client=client)
    assert graph.source == "template"  # cycle is rejected, not executed


@pytest.mark.asyncio
async def test_plan_falls_back_on_duplicate_ids() -> None:
    client = _FakeClient(
        '{"tasks": ['
        '{"task_id": "a", "goal": "ga", "parent_id": null, "capabilities": [], "tools": [], '
        '"verification_method": "none", "timeout_s": 60, "retry_policy": "retry:2"}, '
        '{"task_id": "a", "goal": "gb", "parent_id": null, "capabilities": [], "tools": [], '
        '"verification_method": "none", "timeout_s": 60, "retry_policy": "retry:2"}]}'
    )
    graph = await plan(_read_intent(), client=client)
    assert graph.source == "template"


@pytest.mark.asyncio
async def test_unknown_verification_method_coerced_to_none() -> None:
    client = _FakeClient(
        '{"tasks": [{"task_id": "t", "goal": "g", "parent_id": null, '
        '"capabilities": ["read_vault"], "tools": [], '
        '"verification_method": "llm-judge-thinks-yes", "timeout_s": 60, "retry_policy": "retry:2"}]}'
    )
    graph = await plan(_read_intent(), client=client)
    assert graph.source == "llm"
    assert graph.by_id("t").verification_method == "none"


@pytest.mark.asyncio
async def test_destructive_goal_with_no_verification_is_dropped() -> None:
    """Found 2026-09-13 (beginner-user pass, live): "delete everything please
    help" produced a task {goal: "Delete all data from the vault.",
    tools: ["search_query"], verification_method: "none"} that executed and
    reported ok=true, verdict=pass — search_query cannot delete anything, and
    "none" performs no check at all, so this was a confabulated success on a
    goal the task had no real capability to perform. No tool in this slice
    can actually delete anything, so the only safe behavior is to never let
    such a task claim an unconditional pass. As the sole task, dropping it
    empties the graph, and the caller falls back to the template DAG."""
    client = _FakeClient(
        '{"tasks": [{"task_id": "delete-all-data", '
        '"goal": "Delete all data from the vault.", "parent_id": null, '
        '"capabilities": ["read_vault"], "tools": ["search_query"], '
        '"expected_output": "Confirmation that all data has been deleted.", '
        '"verification_method": "none", "timeout_s": 30, "retry_policy": "retry:2"}]}'
    )
    graph = await plan(_read_intent(), client=client)
    assert graph.source == "template"  # the sole task was dropped -> fallback
    assert not any("delete" in t.task_id for t in graph.tasks)


@pytest.mark.asyncio
async def test_destructive_goal_dropped_but_sibling_tasks_survive() -> None:
    """The drop is per-task, not a blanket reject of the whole run: a
    legitimate sibling task in the same plan is unaffected."""
    client = _FakeClient(
        '{"tasks": ['
        '{"task_id": "research", "goal": "search the vault for X", "parent_id": null, '
        '"capabilities": ["read_vault"], "tools": ["search_query"], '
        '"verification_method": "search_returned_hits", "timeout_s": 60, "retry_policy": "retry:2"}, '
        '{"task_id": "delete-all-data", "goal": "delete everything in the vault", '
        '"parent_id": null, "capabilities": [], "tools": ["search_query"], '
        '"verification_method": "none", "timeout_s": 30, "retry_policy": "retry:2"}]}'
    )
    graph = await plan(_read_intent(), client=client)
    assert graph.source == "llm"
    assert [t.task_id for t in graph.tasks] == ["research"]


@pytest.mark.asyncio
async def test_destructive_goal_with_real_verification_is_kept() -> None:
    """The gate is specifically {destructive language + no grounded check} —
    a destructive-sounding goal paired with a real verification method (one
    the registry can actually run) is not second-guessed here; that's the
    verifier's job, not the parser's."""
    client = _FakeClient(
        '{"tasks": [{"task_id": "cleanup", "goal": "delete everything in scratch/", '
        '"parent_id": null, "capabilities": ["write_file"], "tools": ["vault_write"], '
        '"verification_method": "file_written", "timeout_s": 30, "retry_policy": "retry:2"}]}'
    )
    graph = await plan(_write_intent(), client=client)
    assert graph.source == "llm"
    assert graph.by_id("cleanup").verification_method == "file_written"


@pytest.mark.asyncio
async def test_plan_metrics_move() -> None:
    from prometheus_client.registry import REGISTRY

    def count(event: str) -> float:
        return (
            REGISTRY.get_sample_value("msb_v3_queries_total", {"harness": "agentic", "event": event})
            or 0.0
        )

    before_t = count("plan:template")
    before_l = count("plan:llm")
    await plan(_read_intent(), client=_BrokenClient())
    await plan(_read_intent(), client=_FakeClient('{"tasks": [{"task_id": "t", "goal": "g", "parent_id": null}]}'))
    assert count("plan:template") == before_t + 1
    assert count("plan:llm") == before_l + 1
