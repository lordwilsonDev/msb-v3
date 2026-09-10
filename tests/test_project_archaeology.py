from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "project-archaeology.sh"


def test_project_archaeology_script_is_executable() -> None:
    assert SCRIPT.is_file()
    assert SCRIPT.stat().st_mode & 0o111


def test_project_archaeology_reports_core_inventory() -> None:
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "=== MSB v3 PROJECT ARCHAEOLOGY ===" in result.stdout
    assert "source_files=" in result.stdout
    assert "test_files=" in result.stdout
    assert "--- CI workflows ---" in result.stdout
