"""Shared database path resolution for the governance (brakes) package."""

from __future__ import annotations

from pathlib import Path

from msb_v3.core.config import settings


def default_db_path() -> Path:
    """SQLite DB for all brake state, beside the audit chain in the runtime root.

    Derives from settings.db_path (already verified path-portable by the
    portability gate), so a foreign checkout gets its own governance DB
    without any hardcoded machine paths.
    """
    return Path(settings.db_path).parent / "governance" / "governance.db"
