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

def test_plan_llm_path_parses_valid_tasks() -> None:
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
    graph = plan(_read_intent(), client=client)
    assert graph.source == "llm"
    assert [t.task_id for t in graph.tasks] == ["research", "write"]
    assert graph.by_id("research").timeout_s == 90.0
    assert graph.by_id("write").parent_id == "research"
    assert graph.order()[0].task_id == "research"


def test_plan_falls_back_on_garbage() -> None:
    graph = plan(_read_intent(), client=_FakeClient("sorry, no plan for you"))
    assert graph.source == "template"
    assert graph.tasks[0].task_id == "research"


def test_plan_falls_back_on_unreachable_model() -> None:
    graph = plan(_write_intent(), client=_BrokenClient())
    assert graph.source == "template"
    assert graph.tasks[-1].task_id == "write"  # permissions still honored


def test_plan_falls_back_on_cycle() -> None:
    client = _FakeClient(
        '{"tasks": ['
        '{"task_id": "a", "goal": "ga", "parent_id": "b", "capabilities": [], "tools": [], '
        '"verification_method": "none", "timeout_s": 60, "retry_policy": "retry:2"}, '
        '{"task_id": "b", "goal": "gb", "parent_id": "a", "capabilities": [], "tools": [], '
        '"verification_method": "none", "timeout_s": 60, "retry_policy": "retry:2"}]}'
    )
    graph = plan(_read_intent(), client=client)
    assert graph.source == "template"  # cycle is rejected, not executed


def test_plan_falls_back_on_duplicate_ids() -> None:
    client = _FakeClient(
        '{"tasks": ['
        '{"task_id": "a", "goal": "ga", "parent_id": null, "capabilities": [], "tools": [], '
        '"verification_method": "none", "timeout_s": 60, "retry_policy": "retry:2"}, '
        '{"task_id": "a", "goal": "gb", "parent_id": null, "capabilities": [], "tools": [], '
        '"verification_method": "none", "timeout_s": 60, "retry_policy": "retry:2"}]}'
    )
    graph = plan(_read_intent(), client=client)
    assert graph.source == "template"


def test_unknown_verification_method_coerced_to_none() -> None:
    client = _FakeClient(
        '{"tasks": [{"task_id": "t", "goal": "g", "parent_id": null, '
        '"capabilities": ["read_vault"], "tools": [], '
        '"verification_method": "llm-judge-thinks-yes", "timeout_s": 60, "retry_policy": "retry:2"}]}'
    )
    graph = plan(_read_intent(), client=client)
    assert graph.source == "llm"
    assert graph.by_id("t").verification_method == "none"


def test_plan_metrics_move() -> None:
    from prometheus_client.registry import REGISTRY

    def count(event: str) -> float:
        return (
            REGISTRY.get_sample_value("msb_v3_queries_total", {"harness": "agentic", "event": event})
            or 0.0
        )

    before_t = count("plan:template")
    before_l = count("plan:llm")
    plan(_read_intent(), client=_BrokenClient())
    plan(_read_intent(), client=_FakeClient('{"tasks": [{"task_id": "t", "goal": "g", "parent_id": null}]}'))
    assert count("plan:template") == before_t + 1
    assert count("plan:llm") == before_l + 1
