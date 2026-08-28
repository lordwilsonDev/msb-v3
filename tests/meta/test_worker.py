"""Behaviour pins for msb_v3.meta.worker. render_prompt built by qwen3:8b;
parse_worker_response escalated to the checker. Workers never saw this file."""

from msb_v3.meta.contracts import MSL, WorkerStatus
from msb_v3.meta.worker import parse_worker_response, render_prompt


def test_render_minimal():
    p = render_prompt(MSL(msl_id="m", source_task_id="t", objective="Write add(a,b)"))
    assert "objective: Write add(a,b)" in p
    assert p.strip().endswith("Output only the code, no prose, no markdown fences.")


def test_render_skips_empty_blocks():
    p = render_prompt(MSL(msl_id="m", source_task_id="t", objective="x"))
    assert "allowed actions:" not in p
    assert "constraints:" not in p


def test_render_orders_and_joins():
    p = render_prompt(MSL(
        msl_id="m", source_task_id="t", objective="obj",
        allowed_actions=["read", "write"],
        forbidden_actions=["network"],
        constraints={"max_files": 1, "pure": True},
        verification_commands=["pytest -q", "ruff check ."],
    ))
    assert p.index("objective:") < p.index("allowed actions:") < p.index("forbidden actions:")
    assert p.index("forbidden actions:") < p.index("constraints:") < p.index("must pass:")
    assert "allowed actions: read, write" in p
    assert "forbidden actions: network" in p
    assert "max_files: 1" in p and "pure: True" in p
    assert "must pass: pytest -q, ruff check ." in p


def test_parse_plain_code():
    r = parse_worker_response("def f():\n    return 1\n", "T1", "w")
    assert r.status is WorkerStatus.PRODUCED
    assert r.artifact_ref == "def f():\n    return 1"
    assert r.task_id == "T1" and r.worker_id == "w"


def test_parse_strips_think():
    r = parse_worker_response("<think>hmm let me\nthink</think>\ndef f(): pass", "T1", "w")
    assert r.artifact_ref == "def f(): pass"
    assert r.status is WorkerStatus.PRODUCED


def test_parse_extracts_first_fence():
    txt = "here it is:\n```python\ndef f():\n    return 2\n```\nhope that helps"
    r = parse_worker_response(txt, "T1", "w")
    assert r.artifact_ref == "def f():\n    return 2"


def test_parse_empty_is_no_change():
    r = parse_worker_response("   \n  ", "T1", "w")
    assert r.status is WorkerStatus.NO_CHANGE
    assert r.artifact_ref == ""


def test_parse_think_then_empty_is_no_change():
    r = parse_worker_response("<think>nothing to do</think>   ", "T1", "w")
    assert r.status is WorkerStatus.NO_CHANGE
