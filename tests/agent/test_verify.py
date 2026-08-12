"""Tests for the grounded verifier + failure classifier (msb_v3.agent.verify)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from msb_v3.agent.dag import Task, TaskGraph  # noqa: E402
from msb_v3.agent.verify import classify_failure, verify_task  # noqa: E402


def _task(method: str) -> Task:
    return Task(task_id="t", goal="g", verification_method=method)


# ---------------------------------------------------------------------------
# Grounded checks
# ---------------------------------------------------------------------------

def test_search_returned_hits_list() -> None:
    assert verify_task(_task("search_returned_hits"), {"search_query": [{"id": "a"}]})["ok"] is True
    assert verify_task(_task("search_returned_hits"), {"search_query": []})["ok"] is False
    assert verify_task(_task("search_returned_hits"), {"search_query": "no list"})["ok"] is False


def test_search_returned_hits_dict_shapes() -> None:
    assert verify_task(_task("search_returned_hits"), {"search_query": {"matches": [1, 2]}})["ok"] is True
    assert verify_task(_task("search_returned_hits"), {"search_query": {"results": []}})["ok"] is False


def test_synthesis_nonempty_rejects_fallback() -> None:
    assert verify_task(_task("synthesis_nonempty"), {"chat": "Here is the brief."})["ok"] is True
    assert verify_task(_task("synthesis_nonempty"), {"chat": "[fallback] research the vault"})["ok"] is False
    assert verify_task(_task("synthesis_nonempty"), {"chat": "   "})["ok"] is False
    assert verify_task(_task("synthesis_nonempty"), {"chat": {"text": "brief"}})["ok"] is True


def test_file_written_checks_disk(tmp_path: Path) -> None:
    target = tmp_path / "brief.md"
    target.write_text("content")
    assert verify_task(_task("file_written"), {"vault_write": {"path": str(target)}})["ok"] is True

    missing = tmp_path / "nope.md"
    assert verify_task(_task("file_written"), {"vault_write": {"path": str(missing)}})["ok"] is False

    empty = tmp_path / "empty.md"
    empty.write_text("")
    result = verify_task(_task("file_written"), {"vault_write": {"path": str(empty)}})
    assert result["ok"] is False
    assert "empty" in result["detail"]


def test_file_written_no_path_in_output() -> None:
    assert verify_task(_task("file_written"), {"vault_write": {"status": "ok"}})["ok"] is False


def test_file_written_with_heading_checks_disk(tmp_path: Path) -> None:
    target = tmp_path / "note.md"
    target.write_text("# The Brief\n\nbody\n")
    result = verify_task(_task("file_written_with_heading"), {"vault_write": {"path": str(target)}})
    assert result["ok"] is True
    assert "heading" in result["detail"]

    no_heading = tmp_path / "plain.md"
    no_heading.write_text("body only")
    result = verify_task(_task("file_written_with_heading"), {"vault_write": {"path": str(no_heading)}})
    assert result["ok"] is False

    empty = tmp_path / "empty.md"
    empty.write_text("")
    assert verify_task(_task("file_written_with_heading"), {"vault_write": {"path": str(empty)}})["ok"] is False

    missing = tmp_path / "nope.md"
    assert verify_task(_task("file_written_with_heading"), {"vault_write": {"path": str(missing)}})["ok"] is False


def test_none_method_passes() -> None:
    assert verify_task(_task("none"), {})["ok"] is True


def test_unknown_method_fails() -> None:
    result = verify_task(_task("llm-judge-says-yes"), {})
    assert result["ok"] is False
    assert "unknown" in result["detail"]


def test_receipts_are_grounded_high_trust() -> None:
    """Spec §3.4: every receipt from a real check is kind=grounded, trust=high,
    verdict pass|fail, confidence=1.0. No LLM-judge receipt can ever be a gate."""
    ok_receipt = verify_task(_task("search_returned_hits"), {"search_query": [{"id": "a"}]})
    assert ok_receipt["kind"] == "grounded"
    assert ok_receipt["trust"] == "high"
    assert ok_receipt["check"] == "search_returned_hits"
    assert ok_receipt["verdict"] == "pass"
    assert ok_receipt["confidence"] == 1.0

    fail_receipt = verify_task(_task("search_returned_hits"), {"search_query": []})
    assert fail_receipt["verdict"] == "fail"
    assert fail_receipt["trust"] == "high"

    write_receipt = verify_task(
        _task("file_written_with_heading"),
        {"vault_write": {"path": _temp_note()}},
    )
    assert write_receipt["kind"] == "grounded"
    assert write_receipt["check"] == "file_written_with_heading"

    none_receipt = verify_task(_task("none"), {})
    assert none_receipt["kind"] == "grounded"
    assert none_receipt["trust"] == "high"
    assert none_receipt["verdict"] == "pass"


def _temp_note() -> str:
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
        f.write("# Note\n\nbody\n")
        return f.name


# ---------------------------------------------------------------------------
# Failure classifier
# ---------------------------------------------------------------------------

def _failed_verification(detail: str) -> dict:
    return {"ok": False, "detail": detail}


def test_classify_transient() -> None:
    task = _task("search_returned_hits")
    assert classify_failure(task, {}, _failed_verification("timed out")) == "transient"
    assert classify_failure(task, {}, {}, error="ConnectionError: unreachable") == "transient"


def test_classify_bad_tool_and_bad_retrieval() -> None:
    task = _task("none")
    assert classify_failure(task, {}, _failed_verification("[tool-error] unknown tool: x")) == "bad_tool"
    assert classify_failure(task, {}, _failed_verification("search returned no hits")) == "bad_retrieval"


def test_classify_permission_and_unsafe() -> None:
    task = _task("none")
    assert classify_failure(task, {}, _failed_verification("permission denied")) == "permission"
    assert classify_failure(task, {}, _failed_verification("action blocked: unsafe")) == "unsafe"


def test_classify_unknown_when_no_signal() -> None:
    task = _task("none")
    assert classify_failure(task, {}, _failed_verification("something odd happened")) == "unknown"
    assert classify_failure(task, {}, {}) == "unknown"


# ---------------------------------------------------------------------------
# Executor wiring — the registry runs by default (no LLM judge anywhere)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_executor_uses_grounded_registry_by_default() -> None:
    from msb_v3.agent.executor import execute_graph

    class Provider:
        def __init__(self, result) -> None:
            self._result = result

        async def run_tool(self, name, *, task, inputs, session):
            return self._result

    graph_bad = TaskGraph(
        goal="g",
        tasks=(Task(task_id="r", goal="gr", tools=("search_query",), verification_method="search_returned_hits"),),
    )
    report = await execute_graph(graph_bad, Provider([]))
    assert report.ok is False  # empty search results fail the grounded check

    graph_good = TaskGraph(
        goal="g",
        tasks=(Task(task_id="r", goal="gr", tools=("search_query",), verification_method="search_returned_hits"),),
    )
    report_good = await execute_graph(graph_good, Provider([{"id": "hit"}]))
    assert report_good.ok is True
