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

import pytest

from msb_v3.core.config import settings
from msb_v3.uac import audit_chain as ac


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
def _isolate_wake_and_automation(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the wake inbox/outbox store and the automation manifest/budget
    at per-test scratch files so no test touches data/runtime/wake.db or
    data/runtime/automation/. The automation budget path derives from the
    manifest path's parent, so both land under tmp_path."""
    monkeypatch.setattr(settings, "wake_db_path", str(tmp_path / "runtime" / "wake.db"))
    monkeypatch.setattr(settings, "automation_manifest_path", str(tmp_path / "runtime" / "automation" / "manifest.jsonl"))
    monkeypatch.setattr(settings, "automation_dry_run", True)
