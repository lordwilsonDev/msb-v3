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
    "CI_SERVER_MEMORY_DB", "CI_SERVER_ENV_FILE",
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


def test_runtime_init_resolves_the_repo_env_file(tmp_path: Path) -> None:
    """The env file must be derived from the script's own location, not the
    caller's cwd — verify-release.sh sources this file from a clone's parent.
    """
    command = (
        f"source {SCRIPT}; ci_runtime_init; printf '%s\\n' \"$CI_SERVER_ENV_FILE\""
    )
    result = subprocess.run(
        ["bash", "-c", command], env=_clean_env(tmp_path),
        capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip().splitlines()[-1] == str(ROOT / ".env")


def test_start_server_loads_dotenv_inside_a_subshell() -> None:
    """`python -m msb_v3` alone does not boot on a checkout that has a .env:
    `api/app.py` fails closed (SecretRedactionUnarmed) when .env exists but no
    configured secret resolved, and only scripts/run.sh sources it. CI never saw
    it, because portability staging excludes .env — the run-scoped server only
    ever started where there was nothing to load.

    Static rather than behavioural: booting the real app needs the full
    environment and a health poll, and the property that matters here is
    structural — the load happens in a subshell, so it cannot leak the checkout's
    secrets into the pytest shell.
    """
    text = SCRIPT.read_text(encoding="utf-8")
    start = text.index("ci_runtime_start_server() {")
    body = text[start:]
    assert "(" in body and 'set -a' in body
    assert '. "$CI_SERVER_ENV_FILE"' in body
    assert body.index('set -a') < body.index('. "$CI_SERVER_ENV_FILE"')
    # The redirects must be applied after the load, or a .env value would
    # redirect the run-scoped server back onto the deployment's stores.
    assert body.index('. "$CI_SERVER_ENV_FILE"') < body.index('MSB_DB_PATH="$CI_SERVER_DB"')


def test_start_server_records_the_stores_it_actually_used() -> None:
    """A test that restarts the server has to restore the caller's stores, and
    it cannot infer them from the temp layout: verify-release.sh overrides
    CI_SERVER_RESEARCH to the clone's seeded fixtures. Reconstructing instead of
    recording came back on an empty research root and failed the two
    seeded-harness tests.

    Static, like its neighbours: the record is written on the boot path.
    """
    text = SCRIPT.read_text(encoding="utf-8")
    body = text[text.index("ci_runtime_start_server() {"):]
    assert '>"$CI_RUNTIME_DIR/server.env"' in body
    for key in ("MSB_DB_PATH", "MSB_RESEARCH_ROOT", "MSB_MEMORY_FABRIC_DB_PATH"):
        assert f"printf '{key}=%s" in body, key
    # Written at boot, before the health poll, but never exported to the shell.
    assert body.index("server.env") < body.index('"$MSB_BASE_URL/health"')
    assert "export MSB_RESEARCH_ROOT" not in body


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
