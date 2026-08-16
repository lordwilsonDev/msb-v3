"""Audit-chain unit tests run in dev mode: blank the operator token so the\n``repair()`` logic under test is not gated by ambient env leakage from other\nmodules (the production auth gate is tested explicitly via monkeypatch in\n``test_repair_requires_operator_when_configured``).\n"""

from __future__ import annotations

import pytest

from msb_v3.core.config import settings


@pytest.fixture(autouse=True)
def _dev_mode_operator_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "operator_token", "")
