"""Behaviour pins for msb_v3.meta.verify. classify_check + verdict_from_checks
built by qwen3:8b (verbatim); run_checks is the checker's. Workers never saw this."""

import sys
from pathlib import Path

from msb_v3.meta.contracts import CheckResult, Verdict
from msb_v3.meta.verify import classify_check, run_checks, verdict_from_checks


def test_classify_pass():
    c = classify_check("pytest", 0, "5 passed")
    assert c.passed is True and c.detail == "" and c.name == "pytest"


def test_classify_fail_keeps_tail():
    out = "\n".join(f"line {i}" for i in range(1, 41))
    c = classify_check("pytest", 1, out)
    assert c.passed is False
    assert c.detail.splitlines()[0] == "line 21"
    assert c.detail.splitlines()[-1] == "line 40"
    assert len(c.detail.splitlines()) == 20


def test_classify_fail_short_output():
    c = classify_check("ruff", 2, "E501 line too long\n")
    assert c.passed is False
    assert c.detail == "E501 line too long"


def test_verdict_empty_is_skip():
    assert verdict_from_checks([]) is Verdict.EXPECTED_SKIP


def test_verdict_all_pass():
    assert verdict_from_checks([CheckResult("a", True), CheckResult("b", True)]) is Verdict.PASS


def test_verdict_any_fail():
    assert verdict_from_checks([CheckResult("a", True), CheckResult("b", False)]) is Verdict.FAIL


def test_run_checks_pass(tmp_path: Path):
    vr = run_checks("T1", tmp_path, [f'{sys.executable} -c "exit(0)"'])
    assert vr.verdict is Verdict.PASS
    assert vr.checks[0].passed is True


def test_run_checks_fail_captures_output(tmp_path: Path):
    vr = run_checks("T1", tmp_path, [f'{sys.executable} -c "import sys; sys.stderr.write(\'boom\'); exit(1)"'])
    assert vr.verdict is Verdict.FAIL
    assert "boom" in vr.checks[0].detail


def test_run_checks_empty_is_expected_skip(tmp_path: Path):
    vr = run_checks("T1", tmp_path, [])
    assert vr.verdict is Verdict.EXPECTED_SKIP
