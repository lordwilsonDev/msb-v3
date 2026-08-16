"""Software Factory test fixtures — a real temp repo whose tests actually
run, plus patch scripts that make real changes in the worktree."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def repo(tmp_path: Path):
    """A minimal Python repo with a passing pytest suite."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\npythonpath = [\".\"]\n"
    )
    (root / "app.py").write_text("def add(a, b):\n    return a + b\n")
    (root / "tests").mkdir()
    (root / "tests" / "test_app.py").write_text(
        "from app import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"
    )
    return root


@pytest.fixture()
def good_patch(tmp_path: Path) -> str:
    """Adds a working mul() + its test — tests pass after."""
    script = tmp_path / "good_patch.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'cd "$MSB_WORKTREE"\n'
        "cat >> app.py << 'PY'\ndef mul(a, b):\n    return a * b\nPY\n"
        "cat >> tests/test_app.py << 'PY'\n\n\ndef test_mul():\n    from app import mul\n    assert mul(2, 3) == 6\nPY\n"
    )
    script.chmod(0o755)
    return str(script)


@pytest.fixture()
def breaking_patch(tmp_path: Path) -> str:
    """Breaks add() — tests fail after."""
    script = tmp_path / "breaking_patch.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'cd "$MSB_WORKTREE"\n'
        "sed -i '' 's/return a + b/return a - b/' app.py\n"
    )
    script.chmod(0o755)
    return str(script)


@pytest.fixture()
def noop_patch(tmp_path: Path) -> str:
    """Exits 0 but changes nothing."""
    script = tmp_path / "noop_patch.sh"
    script.write_text("#!/usr/bin/env bash\nexit 0\n")
    script.chmod(0o755)
    return str(script)


@pytest.fixture()
def failing_patch(tmp_path: Path) -> str:
    """Builder exits non-zero — the build itself fails."""
    script = tmp_path / "failing_patch.sh"
    script.write_text("#!/usr/bin/env bash\necho 'build exploded' >&2\nexit 3\n")
    script.chmod(0o755)
    return str(script)
