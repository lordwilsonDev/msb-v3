from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ci-runtime.sh"


def test_runtime_script_never_discovers_or_kills_port_8766() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "lsof" not in text
    assert "8766" not in text
    assert "kill \"$pid\"" in text


def test_start_server_assigns_python_interpreter_not_just_expands() -> None:
    """`: "${VAR:-default}"` expands without assigning — under `set -u` the
    next line dies on an unbound variable. CI regression 2026-08-27."""
    text = SCRIPT.read_text(encoding="utf-8")
    assert 'CI_SERVER_PYTHON="${CI_SERVER_PYTHON:-${MSB_PYTHON:-python3}}"' in text
    assert ': "${CI_SERVER_PYTHON' not in text


def test_runtime_cleanup_is_pid_owned_and_idempotent(tmp_path: Path) -> None:
    command = f"source {SCRIPT}; CI_RUNTIME_DIR={tmp_path}/runtime; mkdir -p $CI_RUNTIME_DIR; sleep 30 & echo $! > $CI_RUNTIME_DIR/server.pid; ci_runtime_cleanup; test ! -e $CI_RUNTIME_DIR"
    subprocess.run(["bash", "-c", command], check=True)


_LEAK_KEYS = {
    "CI_RUNTIME_DIR", "CI_SERVER_PORT", "CI_SERVER_DB", "CI_SERVER_RESEARCH",
    "MSB_PORT", "MSB_DB_PATH", "MSB_RESEARCH_ROOT", "MSB_BASE_URL",
}


def _clean_env(tmp_path: Path) -> dict[str, str]:
    """os.environ minus anything a parent ci_runtime_init would have exported
    (the workflow step sources the script before pytest)."""
    env = {k: v for k, v in os.environ.items() if k not in _LEAK_KEYS}
    env["RUNNER_TEMP"] = str(tmp_path)
    return env


def test_runtime_init_allocates_port_and_private_paths(tmp_path: Path) -> None:
    command = (
        f"source {SCRIPT}; ci_runtime_init; "
        f"printf '%s\\n' \"$CI_SERVER_PORT\" \"$CI_SERVER_DB\" \"$CI_RUNTIME_DIR\""
    )
    result = subprocess.run(
        ["bash", "-c", command], env=_clean_env(tmp_path),
        capture_output=True, text=True, check=True,
    )
    port, db_path, runtime_dir = result.stdout.strip().splitlines()[-3:]
    assert 1024 <= int(port) <= 65535
    assert db_path.startswith(runtime_dir)
    assert runtime_dir.startswith(str(tmp_path))


def test_init_does_not_export_msb_config_to_the_shell(tmp_path: Path) -> None:
    """ci_runtime_init only scopes the server subprocess — the pytest shell
    must keep default Settings. Regression: 2026-08-27 `assert 58665 == 8766`."""
    command = (
        f"source {SCRIPT}; ci_runtime_init; "
        f"printf 'PORT=[%s] DB=[%s] RR=[%s]\\n' "
        f"\"${{MSB_PORT:-}}\" \"${{MSB_DB_PATH:-}}\" \"${{MSB_RESEARCH_ROOT:-}}\""
    )
    result = subprocess.run(
        ["bash", "-c", command], env=_clean_env(tmp_path),
        capture_output=True, text=True, check=True,
    )
    assert "PORT=[] DB=[] RR=[]" in result.stdout, result.stdout
