"""Guard the harness-gate CI wiring — the stale-evidence self-heal must not
silently regress.

harness-gate.yml blocks on stale video-harness evidence (24h window) unless
the pre-flight freshen step re-runs the baseline experiments first. If that
wiring ever regresses (step renamed, moved after the evidence gate, pointed
at the wrong script, or MSB_REPO unbound so it would judge a foreign
checkout), this file fails on the next push instead of silently dropping the
guard — same failure mode as the 2026-08 paths-filter@v3 dead-output bug that
let CI gates skip for weeks unseen.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "harness-gate.yml"
PREFLIGHT = "Pre-flight freshen (harness evidence self-heal)"
EVIDENCE_GATE = "Run webcheck-all (endpoints + harness evidence gate)"


def _gate_steps() -> list[dict]:
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return wf["jobs"]["gate"]["steps"]


def test_preflight_freshen_step_exists_before_evidence_gate() -> None:
    """The freshen step must exist AND run before the evidence gate — a
    freshen step placed after the gate is useless (the gate already judged)."""
    steps = _gate_steps()
    names = [s.get("name") for s in steps]
    assert PREFLIGHT in names, f"missing step {PREFLIGHT!r} in {WORKFLOW.name}"
    assert EVIDENCE_GATE in names, f"missing gate step {EVIDENCE_GATE!r} in {WORKFLOW.name}"
    assert names.index(PREFLIGHT) < names.index(EVIDENCE_GATE), (
        f"{PREFLIGHT!r} must run BEFORE {EVIDENCE_GATE!r}"
    )


def test_preflight_step_calls_freshener_from_checkout() -> None:
    """The step must invoke the freshener script and bind MSB_REPO to the
    checkout workspace so it uses THIS commit's gate logic (not a machine
    path), while HARNESS_DIR stays defaulted to the runner's real
    ~/video-harness."""
    step = next((s for s in _gate_steps() if s.get("name") == PREFLIGHT), None)
    assert step is not None, f"missing step {PREFLIGHT!r}"
    run = step["run"]
    assert "freshen-harness-evidence.sh" in run, f"step does not call the freshener: {run!r}"
    # Best-effort by design: if the freshener fails, the evidence gate below
    # still runs and ships the authoritative report. Pin the actual fallback
    # so a fail-closed rewrite (`|| exit 1`, which would mask that report) is
    # caught, not just a removal of `||`.
    assert "|| {" in run, "freshen step must be best-effort (gate stays authoritative)"
    assert step["env"]["MSB_REPO"] == "${{ github.workspace }}", (
        "MSB_REPO must bind to the checkout so the freshener uses the pushed "
        "commit's gate script"
    )
    # The freshener's correctness depends on HARNESS_DIR defaulting to the
    # runner's REAL ~/video-harness. Binding it to the workspace would judge
    # a fresh checkout's empty evidence dir (NO EVIDENCE -> refresh the wrong
    # dir) while the real evidence gate still blocks — and every assertion
    # above would still pass.
    assert "HARNESS_DIR" not in step.get("env", {}), (
        "HARNESS_DIR must stay defaulted to the runner's ~/video-harness"
    )


def test_freshener_script_is_committed() -> None:
    """The wiring target must exist in the repo — a dangling reference would
    fail the job on the runner but this catches it in the suite first."""
    assert (ROOT / "scripts" / "freshen-harness-evidence.sh").is_file()
