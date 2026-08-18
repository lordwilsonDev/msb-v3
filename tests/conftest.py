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
