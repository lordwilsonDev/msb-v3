"""Guard the evidence-ledger seeding wiring — the claims-review test must keep
running (not skipping) wherever a server is booted from a checkout.

test_harness.py::test_research_assistant_claims_review skips unless the
sovereign-ai-orchestration evidence ledger exists in the checkout's
runtime/. The ledger is gitignored machine state (produced by real research
runs), so fresh checkouts and portability staging copies lack it — this was
the 801-vs-802 suite delta. scripts/seed-evidence-ledger.sh materializes the
committed fixture, and CI / the factory gate / the portability gate must call
it BEFORE booting a server (the server reads the same repo-relative path).
If that wiring ever regresses, the test silently skips again instead of
failing — same failure mode as the 2026-08 paths-filter@v3 dead-output bug
that let CI gates skip for weeks unseen.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SLUG = "sovereign-ai-orchestration"
FIXTURE = (
    ROOT / "tests" / "fixtures" / "evidence_ledgers" / f"{SLUG}_evidence_ledger.json"
)
SEEDER = ROOT / "scripts" / "seed-evidence-ledger.sh"


def _job_steps(wf_path: Path, job: str) -> list[dict]:
    wf = yaml.safe_load(wf_path.read_text(encoding="utf-8"))
    return wf["jobs"][job]["steps"]


def _seed_run_of_server_boot_step(steps: list[dict]) -> str:
    """The step that boots the test server — seeding must precede it in the
    same run block (the server resolves the ledger at request time, so a
    seed in a later step or a different job would still 404)."""
    step = next(
        s for s in steps
        if "python -m msb_v3" in s.get("run", "")
        and ("server.log" in s.get("run", ""))
    )
    run = step["run"]
    assert "seed-evidence-ledger.sh" in run, f"server-boot step does not seed: {run!r}"
    assert run.index("seed-evidence-ledger.sh") < run.index("python -m msb_v3"), (
        "seeder must run BEFORE the server boot (server reads runtime/ at request time)"
    )
    return run


def test_fixture_exists_and_matches_live_shape() -> None:
    assert FIXTURE.is_file(), f"fixture missing: {FIXTURE}"
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    # The endpoint iterates data["claims"] and the test asserts 200 — the
    # fixture must stay structurally compatible with the live ledger.
    assert set(data) == {"evidence", "claims", "meta"}, f"unexpected keys: {list(data)}"


def test_seeder_is_committed() -> None:
    assert SEEDER.is_file()


def test_seeder_materializes_ledger_into_target_root(tmp_path: Path) -> None:
    """Hermetic: seeds into a tmp root — never the real repo runtime/."""
    r = subprocess.run(
        ["bash", str(SEEDER), str(tmp_path)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    ledger = (
        tmp_path / "runtime" / "research" / SLUG / f"{SLUG}_evidence_ledger.json"
    )
    assert ledger.is_file(), "seeder did not write the ledger to ROOT/runtime/research/…"


def test_ci_seeds_before_server_boot() -> None:
    run = _seed_run_of_server_boot_step(_job_steps(ROOT / ".github" / "workflows" / "ci.yml", "test"))
    assert "seed-evidence-ledger.sh" in run


def test_factory_gate_seeds_before_server_boot() -> None:
    run = _seed_run_of_server_boot_step(_job_steps(ROOT / ".github" / "workflows" / "factory-gate.yml", "gate"))
    assert "seed-evidence-ledger.sh" in run


def test_portability_gate_seeds_the_staged_copy() -> None:
    """The portability gate runs the suite from a foreign copy that excludes
    /runtime/ — the seeder must be called against that staged DEST."""
    script = (ROOT / "scripts" / "portability-check.sh").read_text(encoding="utf-8")
    assert "seed-evidence-ledger.sh" in script
    # Seed AFTER staging (the copy must exist first) and BEFORE the suite run
    # (otherwise the test skips in the copy — the delta we're closing). Note
    # the "scripts/test.sh" completeness check precedes the seed, so anchor
    # the before-assertion on the suite-run invocation itself.
    assert script.index("seed-evidence-ledger.sh") > script.index("rsync -a")
    assert script.index("seed-evidence-ledger.sh") < script.index("bash scripts/test.sh")
