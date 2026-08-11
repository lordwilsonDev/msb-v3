"""Hermetic state for guardian tests.

`data/triumvirate/poison_pill.json` is committed runtime state (audit
SMI-017 #6/#10): a `locked_down: true` value committed to the tree starts
every fresh checkout kill-switched, which made
`test_least_privilege_allows_matching_scope` fail outside the original
machine (reproduced by the portability gate). These tests must never read
or write the repo's committed state, so the whole `triumvirate` subdir
redirects its runtime root to a per-session temp dir.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def _hermetic_triumvirate_state(tmp_path_factory) -> Path:
    """Redirect guardian_scanner's runtime files to a pytest-managed tmp dir
    (auto-cleaned) for the whole session and restore them afterward."""
    from msb_v3.triumvirate import guardian_scanner

    tmp = tmp_path_factory.mktemp("triumvirate-state")
    original = (
        guardian_scanner._RUNTIME_ROOT,
        guardian_scanner._SBOM_FILE,
        guardian_scanner._POISON_PILL_FILE,
    )
    guardian_scanner._RUNTIME_ROOT = tmp
    guardian_scanner._SBOM_FILE = tmp / "sbom_registry.json"
    guardian_scanner._POISON_PILL_FILE = tmp / "poison_pill.json"
    yield tmp
    (
        guardian_scanner._RUNTIME_ROOT,
        guardian_scanner._SBOM_FILE,
        guardian_scanner._POISON_PILL_FILE,
    ) = original
