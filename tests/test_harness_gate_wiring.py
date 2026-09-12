"""Guard the harness-gate CI wiring.

DROPPED 2026-09-11: the video-harness evidence stage (`STAGES=...,harness`)
and its pre-flight freshen step. `~/video-harness` does not exist on the
runner's machine and nothing in the repo or vault documents what its
p0_basic/p1_ffmpeg/p2_inference experiments verified, so it could not be
rebuilt without fabricating pass criteria — it had been failing the gate on
every push since the 2026-09-02 repo move. See CLAUDE.archive.md -> "CI
internals" -> "Video-harness evidence stage — DROPPED 2026-09-11" for the
full rationale and the re-enable path.

This file now pins the opposite of what it used to: the harness stage and
its freshener wiring must NOT come back silently (e.g. someone restoring
`STAGES=endpoints,harness` without also restoring the freshen step and the
LaunchAgent would reintroduce the exact "evidence dir doesn't exist ->
gate always fails" class of bug this dropped). A deliberate re-enable
should update this file alongside the workflow.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "harness-gate.yml"
EVIDENCE_GATE = "Run webcheck-all (endpoints)"
PREFLIGHT = "Pre-flight freshen (harness evidence self-heal)"


def _gate_steps() -> list[dict]:
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return wf["jobs"]["gate"]["steps"]


def test_evidence_gate_step_runs_endpoints_only() -> None:
    """The gate step must exist and run STAGES=endpoints — not `,harness`,
    which would silently resurrect a gate that always fails (no
    ~/video-harness on this machine)."""
    steps = _gate_steps()
    step = next((s for s in steps if s.get("name") == EVIDENCE_GATE), None)
    assert step is not None, f"missing step {EVIDENCE_GATE!r} in {WORKFLOW.name}"
    run = step["run"]
    assert "STAGES=endpoints" in run, f"gate step must run STAGES=endpoints: {run!r}"
    assert "harness" not in run, (
        f"harness stage must not be re-added without restoring the freshen "
        f"step + LaunchAgent (see module docstring): {run!r}"
    )


def test_preflight_freshen_step_is_not_present() -> None:
    """The pre-flight freshener step was dropped along with the harness
    stage — nothing left for it to freshen. Its presence without the gate
    also running `harness` would be dead weight; pin it absent instead."""
    names = [s.get("name") for s in _gate_steps()]
    assert PREFLIGHT not in names, (
        f"{PREFLIGHT!r} reappeared without the harness stage being restored "
        "— either restore both together or remove the stray step"
    )


def test_freshener_script_still_committed_but_dormant() -> None:
    """The freshener script stays in the repo (dormant, not deleted) so
    re-enabling later doesn't mean rewriting it from scratch."""
    assert (ROOT / "scripts" / "freshen-harness-evidence.sh").is_file()
