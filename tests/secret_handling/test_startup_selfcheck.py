"""Tests for the startup self-check that refuses a dark redactor.

The check exists because JOB-026 armed the redactor at startup and logged
``seeded: 0`` at INFO — the exact shape of a silent failure. These tests pin
each verdict, the one that must stop startup, and the fact that the *legitimate*
zero cases (fresh clone, CI, deliberate override) still start.

``create_app`` is exercised twice with the check patched, so the wiring is
proven in both directions rather than only in the failing one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from msb_v3.api import app as app_module
from msb_v3.secrets.broker import SEED_ENV_NAMES
from msb_v3.secrets.redact import clear_registry
from msb_v3.secrets.selfcheck import (
    ALLOW_ENV_FLAG,
    EXPECT_ENV_FLAG,
    VERDICT_ARMED,
    VERDICT_EXPECTED,
    VERDICT_OVERRIDDEN,
    VERDICT_UNCONFIGURED,
    VERDICT_UNKNOWN,
    RedactionStatus,
    SecretRedactionUnarmed,
    redaction_self_check,
)

NAMES = ("MSB_SELFCHECK_TEST_KEY",)
SECRET = "TESTONLY_selfcheck_4e91b2ac"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    clear_registry()
    for name in (NAMES[0], EXPECT_ENV_FLAG, ALLOW_ENV_FLAG):
        monkeypatch.delenv(name, raising=False)
    yield
    clear_registry()


def _absent(tmp_path: Path) -> Path:
    """A path that definitely does not exist, so 'expected' comes only from the
    flag under test rather than from this machine's real `.env`."""
    return tmp_path / "no-such-dir" / ".env"


# --- the four real verdicts --------------------------------------------------


def test_armed_when_a_configured_secret_resolves(monkeypatch, tmp_path):
    monkeypatch.setenv(NAMES[0], SECRET)

    status = redaction_self_check(NAMES, env_file=_absent(tmp_path))

    assert status.verdict == VERDICT_ARMED
    assert status.ok is True
    assert status.armed >= 1
    assert status.seeded == NAMES
    assert SECRET not in status.reason, "the reason must never carry the value"


def test_unconfigured_zero_is_not_a_failure(tmp_path):
    """A fresh clone and CI have no secrets and no `.env`: masking is inactive,
    and it hides nothing because there is nothing to hide."""
    status = redaction_self_check(NAMES, env_file=_absent(tmp_path))

    assert status.verdict == VERDICT_UNCONFIGURED
    assert status.ok is True
    assert status.armed == 0
    assert status.expected is False


def test_expected_failure_when_the_machine_is_configured_but_the_process_is_not(tmp_path):
    """The dangerous case: `.env` exists, so secrets exist on this machine, and
    none of them reached this process. Masking would be dark while the app ran."""
    env_file = tmp_path / ".env"
    env_file.write_text("MCP_BRIDGE_SECRET=whatever\n", encoding="utf-8")

    status = redaction_self_check(NAMES, env_file=env_file)

    assert status.verdict == VERDICT_EXPECTED
    assert status.ok is False
    assert status.armed == 0
    # The reason has to be actionable without reading this file.
    assert str(env_file) in status.reason
    assert ALLOW_ENV_FLAG in status.reason


def test_expected_failure_can_also_come_from_the_entrypoint_flag(monkeypatch, tmp_path):
    monkeypatch.setenv(EXPECT_ENV_FLAG, "1")

    status = redaction_self_check(NAMES, env_file=_absent(tmp_path))

    assert status.verdict == VERDICT_EXPECTED
    assert status.ok is False
    assert EXPECT_ENV_FLAG in status.evidence


def test_the_override_starts_anyway_and_says_so(monkeypatch, tmp_path):
    monkeypatch.setenv(EXPECT_ENV_FLAG, "1")
    monkeypatch.setenv(ALLOW_ENV_FLAG, "1")

    status = redaction_self_check(NAMES, env_file=_absent(tmp_path))

    assert status.verdict == VERDICT_OVERRIDDEN
    assert status.ok is True
    assert ALLOW_ENV_FLAG in status.reason
    assert "inactive" in status.reason


def test_the_check_itself_never_raises(monkeypatch, tmp_path):
    """A check that throws would take startup down for a reason other than the
    one it is checking."""
    monkeypatch.setattr(
        "msb_v3.secrets.selfcheck.seed_redaction_from_env",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("registry unavailable")),
    )

    status = redaction_self_check(NAMES, env_file=_absent(tmp_path))

    assert status.verdict == VERDICT_UNKNOWN
    assert status.ok is True
    assert "registry unavailable" in status.reason


def test_a_non_one_flag_value_does_not_count_as_set(monkeypatch, tmp_path):
    """Only ``=1`` means yes — the same convention as the repo's other gates."""
    monkeypatch.setenv(EXPECT_ENV_FLAG, "0")
    monkeypatch.setenv(ALLOW_ENV_FLAG, "true")

    status = redaction_self_check(NAMES, env_file=_absent(tmp_path))

    assert status.verdict == VERDICT_UNCONFIGURED
    assert status.ok is True


# --- wiring: the app refuses, or deliberately does not -----------------------


def _status(verdict: str, *, armed: int = 0) -> RedactionStatus:
    return RedactionStatus(
        verdict=verdict,
        armed=armed,
        seeded=(),
        expected=verdict != VERDICT_UNCONFIGURED,
        evidence="test",
        reason="test reason",
    )


def test_create_app_refuses_to_build_with_the_redactor_dark(monkeypatch):
    monkeypatch.setattr(app_module, "redaction_self_check", lambda: _status(VERDICT_EXPECTED))

    with pytest.raises(SecretRedactionUnarmed) as excinfo:
        app_module.create_app()

    assert "test reason" in str(excinfo.value)


def test_create_app_still_builds_for_the_legitimate_zero_cases(monkeypatch):
    """Fresh clone / CI must not be blocked by a guard aimed at misconfigured
    services — the whole reason the unconfigured verdict is not a failure."""
    monkeypatch.setattr(app_module, "redaction_self_check", lambda: _status(VERDICT_UNCONFIGURED))

    app = app_module.create_app()

    assert app is not None


def test_create_app_reports_the_armed_count_on_the_gauge(monkeypatch):
    monkeypatch.setattr(app_module, "redaction_self_check", lambda: _status(VERDICT_ARMED, armed=3))

    app_module.create_app()

    # Raising an observable signal on /metrics is what makes a dark redactor
    # visible to the cockpit rather than only to whoever reads the log.
    assert app_module.SECRET_REDACTION_ARMED._value.get() == 3


def test_create_app_builds_with_the_override_on_a_configured_machine(monkeypatch):
    """The real conditions of this machine: a repo `.env` exists, so the check
    says *expected*, and pytest never exports those secrets.

    With the override it builds; without it, ``create_app`` raises — which is
    what the autouse fixture above (and, for the whole suite,
    ``tests/conftest.py``) sets the flag for. Asserted in both directions here
    so removing the conftest line fails loudly rather than silently.
    """
    monkeypatch.setenv(ALLOW_ENV_FLAG, "1")
    assert app_module.create_app() is not None

    monkeypatch.delenv(ALLOW_ENV_FLAG, raising=False)
    # Force the misconfigured condition rather than assuming it. Two things in
    # this suite can legitimately arm the redactor before we get here, and both
    # are correct behaviour: the registry is process-global (create_app() runs in
    # 94 test modules), and several tests/security/ modules call load_dotenv() at
    # import time, which puts the real secrets into os.environ for the rest of
    # the session — so the guard correctly does NOT fire. Delete both sources to
    # test the state the guard exists for.
    for name in SEED_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    clear_registry()
    if (Path(app_module.settings.msb_home) / ".env").is_file():
        with pytest.raises(SecretRedactionUnarmed):
            app_module.create_app()
    else:  # a machine with no .env is legitimately unconfigured; must not raise
        assert app_module.create_app() is not None
