"""Guard the research-runtime seeding wiring — the harness suite must keep
running (not skipping) wherever a server is booted from a checkout.

test_harness.py::test_research_assistant_claims_review and
test_research_assistant_ralph_run_served_from_seed skip unless the
sovereign-ai-orchestration evidence ledger / ralph_test STATUS.json exist in
the checkout's runtime/. Runtime artifacts are gitignored machine state
(produced by real research runs), so fresh checkouts and portability staging
copies lack them — this was the 801-vs-802 suite delta.
scripts/seed-research-runtime.sh is slug-agnostic: every
tests/fixtures/research_runtime/<slug>/ dir is seeded into
runtime/research/<slug>/, so a future seeded slug is a data-only change. CI /
the factory gate / the portability gate must call it BEFORE booting a server
(the server reads the same repo-relative paths). If that wiring ever
regresses, the tests silently skip again instead of failing — same failure
mode as the 2026-08 paths-filter@v3 dead-output bug that let CI gates skip
for weeks unseen.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SLUG = "sovereign-ai-orchestration"
RALPH_SLUG = "ralph_test"
FIXTURES_ROOT = ROOT / "tests" / "fixtures" / "research_runtime"
FIXTURE = FIXTURES_ROOT / SLUG / f"{SLUG}_evidence_ledger.json"
RALPH_STATUS = FIXTURES_ROOT / RALPH_SLUG / "STATUS.json"
SEEDER = ROOT / "scripts" / "seed-research-runtime.sh"


def _job_steps(wf_path: Path, job: str) -> list[dict]:
    wf = yaml.safe_load(wf_path.read_text(encoding="utf-8"))
    return wf["jobs"][job]["steps"]


def _seed_run_of_server_boot_step(steps: list[dict]) -> None:
    """The step that boots the test server — seeding must precede it in the
    same run block (the server resolves the artifacts at request time, so a
    seed in a later step or a different job would still 404).

    Note: `next()` pins only the FIRST matching boot step. factory-gate.yml
    has three server-boot sites (coverage pytest / e2e / webcheck) — a new
    boot step added before the pinned one without seeding would escape this
    test, so treat it as coverage of the primary path, not a full audit."""
    step = next(
        s for s in steps
        if "python -m msb_v3" in s.get("run", "")
        and ("server.log" in s.get("run", ""))
    )
    run = step["run"]
    assert "seed-research-runtime.sh" in run, f"server-boot step does not seed: {run!r}"
    assert run.index("seed-research-runtime.sh") < run.index("python -m msb_v3"), (
        "seeder must run BEFORE the server boot (server reads runtime/ at request time)"
    )


def test_fixtures_exist_and_match_live_shape() -> None:
    assert FIXTURE.is_file(), f"ledger fixture missing: {FIXTURE}"
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    # The endpoint iterates data["claims"] and the test asserts 200 — the
    # fixture must stay structurally compatible with the live ledger.
    assert set(data) == {"evidence", "claims", "meta"}, f"unexpected keys: {list(data)}"
    # Ralph fixture mirrors the live machine's ralph_test dir (STATUS.json +
    # identical .bak from the loop's atomic-write discipline).
    assert RALPH_STATUS.is_file(), f"ralph fixture missing: {RALPH_STATUS}"
    assert (FIXTURES_ROOT / RALPH_SLUG / "STATUS.json.bak").is_file()
    assert json.loads(RALPH_STATUS.read_text(encoding="utf-8"))["loop_id"] == "test"


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


def test_seeder_loops_over_all_slugs_including_ralph(tmp_path: Path) -> None:
    """Slug-agnostic: every fixture dir is seeded — evidence-ledger slugs AND
    ralph-loop slugs (STATUS.json), so adding a new slug is data-only."""
    fixtures = tmp_path / "fixtures"
    (fixtures / "alpha").mkdir(parents=True)
    (fixtures / "alpha" / "alpha_evidence_ledger.json").write_text('{"evidence": [], "claims": [], "meta": {}}')
    (fixtures / "beta").mkdir()
    (fixtures / "beta" / "beta_evidence_ledger.json").write_text('{"evidence": [], "claims": [], "meta": {}}')
    (fixtures / "gamma").mkdir()
    (fixtures / "gamma" / "STATUS.json").write_text('{"status": "READY"}')
    root = tmp_path / "root"
    env = {**os.environ, "MSB_RESEARCH_FIXTURES": str(fixtures)}
    r = subprocess.run(
        ["bash", str(SEEDER), str(root)],
        capture_output=True, text=True, env=env,
    )
    assert r.returncode == 0, r.stderr
    for slug, names in (("alpha", ["alpha_evidence_ledger.json"]),
                        ("beta", ["beta_evidence_ledger.json"]),
                        ("gamma", ["STATUS.json"])):
        for name in names:
            artifact = root / "runtime" / "research" / slug / name
            assert artifact.is_file(), f"seeder skipped {slug}/{name}: {artifact}"


def test_seeder_fails_loudly_without_fixtures(tmp_path: Path) -> None:
    """No-op guard: a seeder that silently seeds nothing would let the
    harness tests skip again without a trace — it must fail loudly (both
    guard branches: empty fixtures root, and missing fixtures root)."""
    empty = tmp_path / "empty"
    empty.mkdir()
    for fixtures_root in (empty, tmp_path / "does-not-exist"):
        env = {**os.environ, "MSB_RESEARCH_FIXTURES": str(fixtures_root)}
        r = subprocess.run(
            ["bash", str(SEEDER), str(tmp_path / "root")],
            capture_output=True, text=True, env=env,
        )
        assert r.returncode != 0, f"guard did not fail for {fixtures_root}"
        assert "FAIL" in r.stderr


def test_seeder_never_clobbers_an_existing_ledger(tmp_path: Path) -> None:
    """A bare invocation (ROOT default = repo root) must not overwrite real
    machine state — the reviewer-flagged clobber risk."""
    existing = tmp_path / "runtime" / "research" / SLUG / f"{SLUG}_evidence_ledger.json"
    existing.parent.mkdir(parents=True)
    existing.write_text('{"evidence": [], "claims": ["real run data"], "meta": {}}')
    r = subprocess.run(
        ["bash", str(SEEDER), str(tmp_path)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    assert "real run data" in existing.read_text(), "seeder clobbered an existing ledger"
    assert "not clobbering" in r.stdout


def test_seeder_never_clobbers_an_existing_ralph_status(tmp_path: Path) -> None:
    """Same no-clobber guarantee for the ralph artifact shape."""
    existing = tmp_path / "runtime" / "research" / RALPH_SLUG / "STATUS.json"
    existing.parent.mkdir(parents=True)
    existing.write_text('{"loop_id": "real-run"}')
    r = subprocess.run(
        ["bash", str(SEEDER), str(tmp_path)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    assert "real-run" in existing.read_text(), "seeder clobbered an existing ralph STATUS"
    assert "not clobbering" in r.stdout


def test_ci_seeds_before_server_boot() -> None:
    _seed_run_of_server_boot_step(_job_steps(ROOT / ".github" / "workflows" / "ci.yml", "test"))


def test_factory_gate_seeds_before_server_boot() -> None:
    _seed_run_of_server_boot_step(_job_steps(ROOT / ".github" / "workflows" / "factory-gate.yml", "gate"))


def test_portability_gate_seeds_the_staged_copy() -> None:
    """The portability gate runs the suite from a foreign copy that excludes
    /runtime/ — the seeder must be called against that staged DEST."""
    script = (ROOT / "scripts" / "portability-check.sh").read_text(encoding="utf-8")
    assert "seed-research-runtime.sh" in script
    # Seed AFTER staging (the copy must exist first) and BEFORE the suite run
    # (otherwise the tests skip in the copy — the delta we're closing). Note
    # the "scripts/test.sh" completeness check precedes the seed, so anchor
    # the before-assertion on the suite-run invocation itself.
    assert script.index("seed-research-runtime.sh") > script.index("rsync -a")
    assert script.index("seed-research-runtime.sh") < script.index("bash scripts/test.sh")


def test_portability_suite_run_is_pinned_to_the_copy() -> None:
    """Callers that export MSB_REPO (harness-gate sets it to the runner
    checkout) would redirect test.sh's REPO derivation away from the staged
    copy — the seeded artifacts get bypassed and the foreign-path suite leg
    silently runs the checkout instead. The invocation must pin MSB_REPO to
    $DEST (found 2026-08-13: harness-gate still showed the seeded-run-ledger
    skip because the suite ran the checkout, not the seeded copy)."""
    script = (ROOT / "scripts" / "portability-check.sh").read_text(encoding="utf-8")
    invocation = script[script.index("bash scripts/test.sh") - 120:script.index("bash scripts/test.sh")]
    assert 'MSB_REPO="$DEST"' in invocation, "suite invocation must pin MSB_REPO to the copy"
