"""Black-box CLI tests for scripts/check-env-drift.sh.

Deliberately does not import the script as a module (no sys.path hack) —
same convention as tests/test_verify_claims.py: every test invokes the
script as a subprocess, exactly as the portability gate does. The core
test runs the script's own --selftest (its 7 embedded fixtures) so the
guard is gated inside the pytest suite; the remaining tests exercise the
behavior from the outside (independent of the selftest) and guard the
invariants the gate depends on: drift must warn + fail under --fail, a
clean env must pass, and secret values must never leak into output.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check-env-drift.sh"


def run_drift(*args: str) -> subprocess.CompletedProcess:
    """Invoke the drift guard as the gate does: `bash scripts/...`."""
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )


def test_selftest_passes() -> None:
    """The guard's own 7-fixture selftest must pass inside the suite.

    This is the gate on the gate: a regression in parsing, comparison,
    secret handling, or the --fail path fails here before any push.
    """
    result = run_drift("--selftest")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "selftest PASS" in result.stdout


def test_script_syntax_is_valid() -> None:
    """bash -n catches a syntax break that would make the selftest fail
    confusingly (or silently no-op under an old bash on macOS 3.2)."""
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_mismatch_fails_under_strict_and_never_leaks_secrets(tmp_path) -> None:
    """Independent of the selftest: a mismatched contract var must warn and
    exit 1 under --fail, and a live secret value must never appear in output.
    """
    example = tmp_path / "example.env"
    env = tmp_path / "env.env"
    example.write_text(
        "[TEMPLATE]\n"
        "MSB_PORT=8766\n"
        "OLLAMA_MODEL=qwen3:8b\n"
        "OPENAI_API_KEY=\n",
        encoding="utf-8",
    )
    env.write_text(
        "MSB_PORT=8766\n"
        "OLLAMA_MODEL=qwen3:16b\n"
        "OPENAI_API_KEY=sk-super-secret-value-42\n",
        encoding="utf-8",
    )

    result = run_drift("--fail", str(env), str(example))

    assert result.returncode == 1, result.stdout
    assert "OLLAMA_MODEL differs" in result.stdout
    # The secret's value is compared silently (masked presence only) — the
    # value itself must never be echoed.
    assert "sk-super-secret-value-42" not in result.stdout


def test_clean_env_passes(tmp_path) -> None:
    """A fully-matching env passes with exit 0 in strict mode."""
    example = tmp_path / "example.env"
    env = tmp_path / "env.env"
    example.write_text("[TEMPLATE]\nMSB_PORT=8766\nOLLAMA_MODEL=qwen3:8b\n", encoding="utf-8")
    env.write_text("MSB_PORT=8766\nOLLAMA_MODEL=qwen3:8b\n", encoding="utf-8")

    result = run_drift("--fail", str(env), str(example))

    assert result.returncode == 0, result.stdout
    assert "clean" in result.stdout
    assert "WARN" not in result.stdout
