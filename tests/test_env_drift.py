"""Black-box CLI tests for scripts/check-env-drift.sh.

Deliberately does not import the script as a module (no sys.path hack) —
same convention as tests/test_verify_claims.py: every test invokes the
script as a subprocess, exactly as the portability gate does. The core
test runs the script's own --selftest (its 7 embedded fixtures) so the
guard is gated inside the pytest suite; the remaining tests exercise the
behavior from the outside (independent of the selftest) and guard the
invariants the gate depends on: drift must warn + fail under --fail, a
clean env must pass, and secret values must never leak into output.

It also pins the storage invariants of the TypeSafe (Jev) key, which by
operator disposition (2026-09-20) is **stored but deliberately unwired**:
declared in the template, masked in the guard, and not yet consumed by any
runtime module. See board decision 14.
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


# --- TypeSafe (Jev) key: stored, deliberately not wired -------------------

KEY = "TYPESAFE_API_KEY"
SRC = REPO_ROOT / "src" / "msb_v3"


def test_typesafe_key_is_declared_and_masked() -> None:
    """A credential that is neither documented nor masked is one that will be
    leaked by the drift check on the day it is rotated."""
    example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    assert f"{KEY}=" in example, f"{KEY} must be declared in .env.example"

    guard = (REPO_ROOT / "scripts" / "check-env-drift.sh").read_text(encoding="utf-8")
    secret_keys = guard.split("SECRET_KEYS=(", 1)[1].split(")", 1)[0].split()
    assert KEY in secret_keys, (
        f"{KEY} must be listed in SECRET_KEYS or the drift check may print its value"
    )


def test_typesafe_key_is_not_yet_consumed_by_runtime_code() -> None:
    """The key is stored, not wired. This is the tripwire for that deferral.

    It fires in the change that integrates the key — which is the point: the
    operator deferred evaluation until the deliverables are green and required
    a re-issue before production, so the integration commits must also update
    board decision 14 and replace this test rather than silently relying on it.

    **Re-examined 2026-09-20 (JOB-026), and narrowed rather than deleted.** It
    fired when ``msb_v3/secrets`` began *seeding* the key into the redactor —
    which is not consuming the credential: nothing resolves it, and the seed
    list is precisely what masks the key on every output channel. The scope is
    therefore "outside the secrets package". A module that reads this key to
    call the vendor still fails this test, which is what the deferral needs.
    """
    offenders = sorted(
        p.relative_to(REPO_ROOT).as_posix()
        for p in SRC.rglob("*.py")
        if "TYPESAFE" in p.read_text(encoding="utf-8") and not p.is_relative_to(SRC / "secrets")
    )
    assert offenders == [], (
        f"runtime code now references {KEY}: {offenders}. If that is the "
        "intended integration, update board decision 14 and replace this test."
    )
