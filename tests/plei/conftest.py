"""Shared fixtures for PLEI tests — project root that works under xdist."""
from __future__ import annotations

from pathlib import Path

import pytest


def _find_project_root() -> Path:
    """Walk up from cwd to find pyproject.toml — works under xdist."""
    d = Path.cwd()
    for _ in range(10):
        if (d / "pyproject.toml").exists():
            return d
        d = d.parent
    return Path.cwd()


PLEI_ROOT = _find_project_root()


@pytest.fixture
def project_root() -> Path:
    """Absolute path to the msb-v3 project root."""
    return PLEI_ROOT
