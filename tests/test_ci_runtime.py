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
    assert 'CI_SERVER_PYTHON="${CI_SERVER_PYTHON:-${MSB_PYTHON:-python}}"' in text
    assert ': "${CI_SERVER_PYTHON' not in text


def test_start_server_runs_without_unbound_variable(tmp_path: Path) -> None:
    """Exercise ci_runtime_start_server far enough to prove no `set -u`
    unbound-variable abort before the health poll (the missed regression)."""
    command = (
        f"source {SCRIPT}; RUNNER_TEMP={tmp_path} ci_runtime_init; "
        f"CI_SERVER_PYTHON=true ci_runtime_start_server; echo rc=$?"
    )
    result = subprocess.run(
        ["bash", "-c", command], capture_output=True, text=True,
        env={**os.environ, "RUNNER_TEMP": str(tmp_path)},
    )
    combined = result.stdout + result.stderr
    assert "unbound variable" not in combined, combined
    # `true` exits 0 immediately, so the server is "gone" and the helper
    # returns non-zero after its own diagnostic — that is the expected path.
    assert "server exited" in combined or "did not become healthy" in combined


def test_runtime_cleanup_is_pid_owned_and_idempotent(tmp_path: Path) -> None:
    command = f"source {SCRIPT}; CI_RUNTIME_DIR={tmp_path}/runtime; mkdir -p $CI_RUNTIME_DIR; sleep 30 & echo $! > $CI_RUNTIME_DIR/server.pid; ci_runtime_cleanup; test ! -e $CI_RUNTIME_DIR"
    subprocess.run(["bash", "-c", command], check=True)


def test_runtime_init_allocates_port_and_private_paths(tmp_path: Path) -> None:
    command = f"source {SCRIPT}; ci_runtime_init; printf '%s\\n' \"$MSB_PORT\" \"$MSB_DB_PATH\" \"$CI_RUNTIME_DIR\""
    result = subprocess.run(
        ["bash", "-c", command],
        env={**os.environ, "RUNNER_TEMP": str(tmp_path)},
        capture_output=True,
        text=True,
        check=True,
    )
    port, db_path, runtime_dir = result.stdout.strip().splitlines()[-3:]
    assert 1024 <= int(port) <= 65535
    assert db_path.startswith(runtime_dir)
    assert runtime_dir.startswith(str(tmp_path))
