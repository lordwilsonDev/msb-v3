"""CLI tests: python -m msb_v3.moie policy.

Pins the detection-policy surface (MSB-CAL-006/007):

- validation uses the same fail-closed loader as the engine (missing /
  corrupt / incomplete policy -> exit 1, no coverage computed);
- committed-policy coverage matches the pinned baseline exactly
  (MSB-GATE-EVAL-001: 17/8/8/23, precision 0.68, recall 0.425);
- a candidate policy diffs against the committed one (newly blocked /
  newly missed ids) and --strict exits 2 on drift — the CI hook;
- the local flow enforces the same gate before CI does: `make policy-gate`
  (and `make lint`, which mirrors CI's lint job) plus the pre-push hook.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from msb_v3.moie.cli import PINNED_BASELINE, main
from msb_v3.moie.experts import apply_policy_overrides, risk_policy_path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_JSON = ROOT / "config" / "risk_templates.json"
GATE_SCRIPT = ROOT / "scripts" / "ci-policy-gate.sh"


@pytest.fixture(autouse=True)
def _restore_committed_policy():
    """The diff path mutates module-level built-in experts (the loader is
    atomic per call but the final state is the candidate). Restore the
    committed policy after every test so no test leaks state."""
    yield
    apply_policy_overrides(risk_policy_path())


def _candidate_with_extra_danger(tmp_path: Path, *extra: str) -> Path:
    data = json.loads(TEMPLATES_JSON.read_text(encoding="utf-8"))
    data["experts"]["security"]["keywords"]["danger"] += list(extra)
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# ── committed policy: validate + baseline match ─────────────────────────────

def test_policy_committed_validates_and_matches_baseline(capsys) -> None:
    assert main(["policy"]) == 0
    out = capsys.readouterr().out
    assert "[moie] policy" in out
    assert "baseline MSB-GATE-EVAL-001: MATCH" in out
    assert "precision 0.68" in out
    assert "recall 0.425" in out
    assert "(tp=17 fp=8 tn=8 fn=23)" in out


def test_policy_json_shape(capsys) -> None:
    assert main(["policy", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["policy"]["valid"] is True
    assert payload["corpus"]["total"] == 56
    assert {k: payload["coverage"][k] for k in ("tp", "fp", "tn", "fn")} == PINNED_BASELINE
    assert payload["baseline"]["match"] is True
    assert payload["diff"] is None
    assert set(payload["categories"]) == {
        "dangerous", "benign_danger_word", "ambiguous", "obfuscated", "encoded", "multilingual",
    }


# ── fail-closed validation ──────────────────────────────────────────────────

def test_policy_corrupt_fails_closed(capsys, tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert main(["policy", "--policy", str(bad)]) == 1
    err = capsys.readouterr().err
    assert "validation FAILED" in err
    assert "corrupt" in err


def test_policy_missing_fails_closed(capsys, tmp_path: Path) -> None:
    assert main(["policy", "--policy", str(tmp_path / "nope.json")]) == 1
    assert "validation FAILED" in capsys.readouterr().err


def test_policy_incomplete_fails_closed(capsys, tmp_path: Path) -> None:
    data = json.loads(TEMPLATES_JSON.read_text(encoding="utf-8"))
    del data["experts"]["security"]
    bad = tmp_path / "incomplete.json"
    bad.write_text(json.dumps(data), encoding="utf-8")
    assert main(["policy", "--policy", str(bad)]) == 1
    assert "missing entry for expert 'security'" in capsys.readouterr().err


# ── candidate diff: the edit-test-commit loop ───────────────────────────────

def test_policy_candidate_diff_reports_new_blocks(capsys, tmp_path: Path) -> None:
    cand = _candidate_with_extra_danger(
        tmp_path, "disable authentication", "escalate privileges", "exfiltrate"
    )
    assert main(["policy", "--policy", str(cand)]) == 0
    out = capsys.readouterr().out
    # the Phase-2 keyword win: D3/D11/D15 newly caught, no new misses
    assert "+ blocked: D11 D15 D3" in out
    assert "- missed:  (none)" in out
    assert "baseline MSB-GATE-EVAL-001: DRIFT" in out
    assert "precision 0.7143" in out
    assert "(tp=20 fp=8 tn=8 fn=20)" in out


def test_policy_candidate_diff_json(capsys, tmp_path: Path) -> None:
    cand = _candidate_with_extra_danger(tmp_path, "exfiltrate")
    assert main(["policy", "--policy", str(cand), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["policy"]["path"] == str(cand)
    assert payload["diff"]["newly_blocked"] == ["D15"]
    assert payload["diff"]["newly_missed"] == []
    assert payload["baseline"]["match"] is False
    assert payload["coverage"]["tp"] == 18


def test_policy_candidate_incomplete_still_fails_closed(capsys, tmp_path: Path) -> None:
    """The candidate path validates with the SAME fail-closed loader —
    an incomplete candidate never reaches the coverage step."""
    cand = _candidate_with_extra_danger(tmp_path, "exfiltrate")
    data = json.loads(cand.read_text(encoding="utf-8"))
    del data["experts"]["governance"]
    cand.write_text(json.dumps(data), encoding="utf-8")
    assert main(["policy", "--policy", str(cand)]) == 1
    assert "missing entry for expert 'governance'" in capsys.readouterr().err


# ── strict mode: the CI hook ────────────────────────────────────────────────

def test_policy_strict_drift_exits_2(capsys, tmp_path: Path) -> None:
    cand = _candidate_with_extra_danger(tmp_path, "exfiltrate")
    assert main(["policy", "--policy", str(cand), "--strict"]) == 2
    assert "drifted from pinned baseline" in capsys.readouterr().err


def test_policy_strict_match_exits_0(capsys) -> None:
    assert main(["policy", "--strict"]) == 0


# ── state hygiene: candidate runs must not leak into later runs ─────────────

def test_policy_state_restored_after_candidate_run(capsys, tmp_path: Path) -> None:
    cand = _candidate_with_extra_danger(tmp_path, "exfiltrate")
    assert main(["policy", "--policy", str(cand)]) == 0
    # the autouse fixture restored the committed policy — a fresh run must
    # match the baseline again (no cross-test contamination)
    assert main(["policy"]) == 0
    assert "baseline MSB-GATE-EVAL-001: MATCH" in capsys.readouterr().out


# ── the CI gate script (scripts/ci-policy-gate.sh) ──────────────────────────

def _run_gate(env_extra: dict | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update({"MSB_REPO": str(ROOT), "MSB_PYTHON": sys.executable})
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["bash", str(GATE_SCRIPT)],
        capture_output=True, text=True, timeout=120, env=env, cwd=str(cwd or ROOT),
    )


def test_ci_gate_passes_on_committed_policy() -> None:
    '''The gate must pass on the committed policy — that is the CI green
    state. Also proves CWD portability: run from a foreign directory.'''
    r = _run_gate(cwd=ROOT / "tests")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "baseline MSB-GATE-EVAL-001: MATCH" in r.stdout


def test_ci_gate_fails_on_drift_via_env_override(tmp_path: Path) -> None:
    '''A policy edit that changes detection coverage must fail the build
    (exit 2) until the pins are updated with it. The env override points
    the gate at a candidate diffed against the committed policy.'''
    cand = _candidate_with_extra_danger(tmp_path, "exfiltrate")
    r = _run_gate({"MSB_RISK_POLICY_PATH": str(cand)})
    assert r.returncode == 2, r.stdout + r.stderr
    assert "DRIFT" in r.stdout
    assert "+ blocked: D15" in r.stdout


def test_ci_gate_fails_on_invalid_policy(tmp_path: Path) -> None:
    '''A corrupt policy must fail the gate (exit 1) — never pass because
    coverage could not be computed.'''
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    r = _run_gate({"MSB_RISK_POLICY_PATH": str(bad)})
    assert r.returncode == 1, r.stdout + r.stderr
    assert "validation FAILED" in r.stderr


def test_ci_gate_strict_downgrade_warns_but_passes() -> None:
    '''MSB_STRICT=0 is the explicit dry-run escape hatch: drift becomes a
    warning and the build stays green. Never the default — only set by a
    developer who knows what they are doing.'''
    r = _run_gate({"MSB_STRICT": "0"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "downgraded to a warning" in r.stdout


# ── the local flow: Makefile target + pre-push hook ─────────────────────────

MAKEFILE = ROOT / "Makefile"
HOOK_SRC = ROOT / "scripts" / "hooks" / "pre-push"
HOOK_INSTALLED = ROOT / ".git" / "hooks" / "pre-push"

# The Makefile hardcodes the Mac interpreter (PY := miniforge), so the make
# flow is a local-dev surface by design — CI (ubuntu) runs the gate directly
# as a workflow step, covered by the _run_gate tests above. The make tests
# verify the local flow where it is the tool of record.
_DARWIN_ONLY = pytest.mark.skipif(
    sys.platform != "darwin",
    reason="make flow is Mac-local (hardcoded miniforge PY); CI runs the gate as a workflow step",
)


def _run_make(args: list[str], env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["make", *args], capture_output=True, text=True, timeout=120, env=env, cwd=str(ROOT),
    )


@_DARWIN_ONLY
def test_make_policy_gate_target_passes() -> None:
    '''`make policy-gate` is the local equivalent of the CI gate — it must
    pass on the committed policy so it can be run before any push.'''
    r = _run_make(["policy-gate"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "baseline MSB-GATE-EVAL-001: MATCH" in r.stdout


@_DARWIN_ONLY
def test_make_policy_gate_fails_on_drift(tmp_path: Path) -> None:
    '''A policy edit that drifts coverage must fail `make policy-gate`
    exactly like the CI gate (exit 2) — the failure mode lands locally,
    before CI ever sees it. The env override flows through make untouched
    (the target only pins MSB_PYTHON).'''
    cand = _candidate_with_extra_danger(tmp_path, "exfiltrate")
    r = _run_make(["policy-gate"], {"MSB_RISK_POLICY_PATH": str(cand)})
    assert r.returncode == 2, r.stdout + r.stderr
    assert "DRIFT" in r.stdout
    assert "+ blocked: D15" in r.stdout


@_DARWIN_ONLY
def test_make_lint_dry_run_includes_policy_gate() -> None:
    '''`make lint` is documented as "the exact gates CI's lint job runs" —
    CI's lint job runs the policy drift gate, so the local mirror must too
    (this is what makes the pre-push hook enforce it before every push:
    the hook calls `make lint`).'''
    r = _run_make(["lint", "-n"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ci-policy-gate.sh" in r.stdout


def test_prepush_hook_runs_policy_gate() -> None:
    '''The versioned hook must enforce the gate on every push: through
    `make lint` when a Makefile exists, and via the gate script directly
    in the no-Makefile fallback (foreign clone).'''
    src = HOOK_SRC.read_text(encoding="utf-8")
    assert "make lint" in src          # Makefile path (lint now includes the gate)
    assert "ci-policy-gate.sh" in src  # no-Makefile fallback path
    assert "policy drift" in src


def test_installed_hook_matches_versioned() -> None:
    '''`make hooks-install` keeps .git/hooks/pre-push in sync with the
    versioned scripts/hooks/pre-push — a stale installed hook would
    silently skip gates the repo has since added. Skips on fresh clones
    (no hook installed yet; CI runs its own workflow gates).'''
    if not HOOK_INSTALLED.exists():
        pytest.skip("no installed hook on a fresh clone")
    assert HOOK_INSTALLED.read_text(encoding="utf-8") == HOOK_SRC.read_text(encoding="utf-8")
