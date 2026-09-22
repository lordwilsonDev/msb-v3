"""Suite-wide isolation: never let a default-chain append touch production.

Components that fall back to ``anchored_chain_from_env()`` / a bare
``AuditChain()`` when no chain is injected (agent trace/safety, the
governance guard, the chat harness) resolve the "default chain" from a
CWD-relative path — under the repo that is the LIVE production chain
(data/uac/audit_chain.db), which is also anchor-protected. Point every
test's default chain at a per-test scratch file instead; tests that want a
specific chain still inject one or monkeypatch ``_AUDIT_DB`` themselves.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from msb_v3.core.config import settings
from msb_v3.uac import audit_chain as ac

_REPO_ROOT = Path(__file__).resolve().parents[1]

# The startup redaction self-check refuses to build the app when a *configured*
# process would mask nothing — and a machine with a repo `.env` looks configured
# to it even though pytest never exports those secrets. 94 test modules call
# create_app(), so the suite accepts an unarmed start deliberately here. This
# does not hide the state: the app still logs a WARNING and reports
# msb_v3_secret_redaction_armed=0, and the guard itself is covered by
# tests/secret_handling/test_startup_selfcheck.py. The guard protects the
# service; it should not fight the suite.
os.environ.setdefault("MSB_ALLOW_UNARMED_REDACTION", "1")

# Test tiers, per pyproject [tool.pytest.ini_options]. All three tier markers
# mean "this test needs something this machine may not have running":
# integration expects a live msb-v3 on MSB_BASE_URL / :8766, live hits a real
# Ollama endpoint, chaos spawns the h08 fault-injection proxy subprocess.
#
# The pyproject comment has always said the release gate runs the hermetic
# core and that these tiers run separately — but nothing enforced it, so the
# daily factory gate ran all 75 tier tests inline against the shared dev
# instance. That made the gate's verdict a function of machine state rather
# than of the commit. Concretely: test_cold_state_verification.py SIGKILLs
# whatever owns the target port and re-spawns it mid-suite (it is *testing*
# restart recovery), so whether each later integration test reached a live
# server or found the gap came down to timing. That file now refuses to kill
# the default port unless MSB_ALLOW_RESTART_LIVE=1 is set deliberately, which
# removes the worst form of this but not the general one: the tiers still need
# a live server, so they stay deselected by default. Measured against one
# unchanged tree:
# 3351 passed / 0 failed with the server up throughout, 3317 passed / 3 failed
# when it blinked mid-run, and the gate's own run logged 1 failed + 20 errors.
#
# Deselecting them by default is deterministic and costs no coverage (84%
# either way, against an 80% floor) — the factory's own hygiene members still
# exercise the live contract. Opt back in for a deliberate tier run with
# MSB_RUN_TIERS=1 (see `make test-tiers`).
_TIER_MARKERS = ("integration", "chaos", "live")


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Keep a default collection hermetic by dropping the live tiers."""
    if os.environ.get("MSB_RUN_TIERS") == "1":
        return
    keep: list[pytest.Item] = []
    deselected: list[pytest.Item] = []
    for item in items:
        if any(item.get_closest_marker(m) for m in _TIER_MARKERS):
            deselected.append(item)
        else:
            keep.append(item)
    if not deselected:
        return
    config.hook.pytest_deselected(items=deselected)
    items[:] = keep


@pytest.fixture(autouse=True)
def _isolate_default_audit_chain(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ac, "_AUDIT_DB", tmp_path / "uac" / "audit_chain.db")


@pytest.fixture(autouse=True)
def _isolate_default_spine(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the default decision-spine DB at a per-test scratch file, so a
    provider/container constructing DecisionEvidenceStore() with no explicit
    path never touches data/evidence/decision_spine.db during tests."""
    monkeypatch.setattr(settings, "decision_spine_db_path", str(tmp_path / "evidence" / "decision_spine.db"))


@pytest.fixture(autouse=True)
def _disable_cron_scheduler(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Never spawn the cron heartbeat loop during tests: the FastAPI lifespan
    starts it when settings.cron_enabled, and an unattended background loop
    could fire real jobs (backups, exports) against the live deployment.
    Tests that exercise the scheduler drive it explicitly. Also points the
    default cron DB at the per-test scratch dir so any CronStore() without an
    explicit path never touches data/runtime/cron.db."""
    monkeypatch.setattr(settings, "cron_enabled", False)
    monkeypatch.setattr(settings, "cron_db_path", str(tmp_path / "cron.db"))


@pytest.fixture(autouse=True)
def _isolate_governance_db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point governance/killswitch state at a per-test scratch DB.

    Found 2026-09-12 (chaos test): governance.db.default_db_path() derives
    from settings.db_path, and a fresh KillSwitch() with no explicit path
    resolves there. Before this fixture, any test exercising a code path
    that constructs KillSwitch() with no override picked up whatever the
    REAL operator has armed on this machine (e.g. the real vault_write
    scope armed since 2026-09-02) -- tests passed or failed depending on
    unrelated live state, not the code under test. Isolating db_path here
    (same pattern as the other _isolate_* fixtures in this file) means
    settings.db_path always needs a matching override in any test that
    then asserts on decision_spine_db_path/cron_db_path/etc. deriving from
    it, since this fixture wins over anything set before it runs -- see
    the other _isolate_* fixtures for the same constraint."""
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "msb_v3.db"))


@pytest.fixture(autouse=True)
def _isolate_wake_and_automation(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the wake inbox/outbox store and the automation manifest/budget
    at per-test scratch files so no test touches data/runtime/wake.db or
    data/runtime/automation/. The automation budget path derives from the
    manifest path's parent, so both land under tmp_path."""
    monkeypatch.setattr(settings, "wake_db_path", str(tmp_path / "runtime" / "wake.db"))
    monkeypatch.setattr(settings, "automation_manifest_path", str(tmp_path / "runtime" / "automation" / "manifest.jsonl"))
    monkeypatch.setattr(settings, "automation_dry_run", True)


@pytest.fixture(autouse=True)
def _isolate_memory_fabric_db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the default Memory Fabric DB at a per-test scratch file.

    Added 2026-09-12 alongside container.py wiring memory_fabric into the
    default build_container() construction (previously only api/memory_fabric.py
    built one, standalone) — without this, any test that builds a real
    container with no memory_fabric override would write into
    data/memory_fabric/memory.db, the same class of gap the chaos test found
    for settings.db_path (see _isolate_governance_db above)."""
    monkeypatch.setattr(settings, "memory_fabric_db_path", str(tmp_path / "memory_fabric" / "memory.db"))


def _run_scoped_stores() -> dict[str, str]:
    """The stores the run-scoped server was actually started with.

    `scripts/ci-runtime.sh` redirects the server's stores but deliberately does
    not export them — exporting MSB_DB_PATH would redirect the pytest process's
    own Settings (see the tier note above). It records them in
    $CI_RUNTIME_DIR/server.env instead. Tests that assert on rows the *server*
    wrote must read the server's store: under `make release-verify` the scoped
    server writes to a temp dir, so a test reading `<repo>/data/...` asserts
    against a file the server never touched.
    """
    runtime_dir = os.environ.get("CI_RUNTIME_DIR")
    path = os.path.join(runtime_dir, "server.env") if runtime_dir else None
    values: dict[str, str] = {}
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                key, sep, value = line.rstrip("\n").partition("=")
                if sep:
                    values[key] = value
    return values


@pytest.fixture
def server_fabric_db_path() -> str:
    """Memory-fabric DB of the server under test (the one at MSB_BASE_URL).

    Four integration files used to hardcode `<repo>/data/memory_fabric/...`
    instead. That was wrong in two directions at once: it wrote into the live
    deployment when run locally (the autouse `_isolate_memory_fabric_db` above
    exists precisely to stop that), and it read a file the run-scoped server
    never wrote once ci-runtime.sh added the fabric redirect.
    """
    explicit = os.environ.get("MSB_MEMORY_FABRIC_DB_PATH")
    if explicit:
        return explicit
    scoped = _run_scoped_stores().get("MSB_MEMORY_FABRIC_DB_PATH")
    if scoped:
        return scoped
    return str(_REPO_ROOT / "data" / "memory_fabric" / "memory.db")
