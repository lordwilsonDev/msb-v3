"""Behaviour pins for msb_v3.meta.loop.build_module — driven with a fake
model_call so no local model is needed."""

import sys
from pathlib import Path

from msb_v3.meta.contracts import MSL, Verdict, WorkerStatus
from msb_v3.meta.loop import build_module

PYOK = f'{sys.executable} -c "import mod; assert mod.answer() == 42"'


def _msl() -> MSL:
    return MSL(
        msl_id="M1", source_task_id="T1",
        objective="write answer() -> 42",
        verification_commands=[PYOK],
    )


def test_first_attempt_passes(tmp_path: Path):
    calls = []

    def model(prompt: str) -> str:
        calls.append(prompt)
        return "def answer():\n    return 42\n"

    out = build_module(_msl(), tmp_path, "mod.py", model_call=model, verify_commands=[PYOK])
    assert out.ok and out.verdict is Verdict.PASS
    assert out.attempts == 1
    assert len(calls) == 1
    assert (tmp_path / "mod.py").read_text().startswith("def answer():")
    assert out.worker_results[0].status is WorkerStatus.PRODUCED


def test_correction_loop_recovers(tmp_path: Path):
    seq = iter([
        "def answer():\n    return 0\n",     # wrong
        "def answer():\n    return 42\n",    # fixed after correction
    ])

    prompts = []

    def model(prompt: str) -> str:
        prompts.append(prompt)
        return next(seq)

    out = build_module(_msl(), tmp_path, "mod.py", model_call=model,
                       verify_commands=[PYOK], max_attempts=3)
    assert out.ok and out.attempts == 2
    assert len(out.failures) == 1
    assert out.failures[0].repair_scope == ["mod.py"]
    # the 2nd prompt carried the correction suffix
    assert "did not pass these checks" in prompts[1]


def test_exhausts_attempts_and_reports_fail(tmp_path: Path):
    def model(prompt: str) -> str:
        return "def answer():\n    return 0\n"

    out = build_module(_msl(), tmp_path, "mod.py", model_call=model,
                       verify_commands=[PYOK], max_attempts=2)
    assert not out.ok and out.verdict is Verdict.FAIL
    assert out.attempts == 2
    assert len(out.failures) == 2


def test_model_error_is_recorded_not_raised(tmp_path: Path):
    def model(prompt: str) -> str:
        raise RuntimeError("ollama down")

    out = build_module(_msl(), tmp_path, "mod.py", model_call=model, verify_commands=[PYOK])
    assert not out.ok
    assert out.worker_results[0].status is WorkerStatus.ERROR
    assert out.worker_results[0].error_class == "RuntimeError"


def test_no_code_produced_stops(tmp_path: Path):
    def model(prompt: str) -> str:
        return "   "

    out = build_module(_msl(), tmp_path, "mod.py", model_call=model, verify_commands=[PYOK])
    assert not out.ok
    assert out.worker_results[0].status is WorkerStatus.NO_CHANGE
    assert out.verifications == []
