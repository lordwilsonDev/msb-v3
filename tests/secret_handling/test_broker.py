"""Tests for the SecretBroker seam — the four roles, and the swap that makes it
a seam rather than a class.

The seam rule being verified (interchangeable-components): a consumer programs
against ``SecretBroker`` the Definition, and the registry can move it from one
provider to another without the consumer changing. The last section is the
required **swap test**.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from msb_v3.secrets.broker import (
    SEED_ENV_NAMES,
    EnvSecretBroker,
    KeychainSecretBroker,
    Secret,
    SecretBrokerRegistry,
    SecretRef,
    SecretUnavailable,
    UnavailableSecretBroker,
    default_registry,
    seed_redaction_from_env,
)
from msb_v3.secrets.redact import clear_registry, redact, registered_values

SECRET = "TESTONLY_broker_acb4e21f9d"
REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_registry()
    yield
    clear_registry()


# --- the reference is safe to hold, the value is not ------------------------


def test_a_reference_round_trips_and_prints_as_a_uri():
    ref = SecretRef.parse("secret://env/OPENAI_API_KEY")
    assert ref.scheme == "env" and ref.name == "OPENAI_API_KEY"
    assert str(ref) == "secret://env/OPENAI_API_KEY"
    assert str(SecretRef.env("X")) == "secret://env/X"


@pytest.mark.parametrize("bad", ["OPENAI_API_KEY", "secret://", "secret://env", "secret:///NAME"])
def test_a_malformed_reference_is_refused(bad):
    with pytest.raises(ValueError):
        SecretRef.parse(bad)


def test_a_secret_refuses_to_print_itself(monkeypatch):
    """The point of the wrapper: a stray log or f-string cannot leak it."""
    monkeypatch.setenv("BROKER_TEST_KEY", SECRET)
    secret = EnvSecretBroker().resolve(SecretRef.env("BROKER_TEST_KEY"))

    assert SECRET not in str(secret)
    assert SECRET not in repr(secret)
    assert SECRET not in f"{secret}"
    assert SECRET not in f"{secret!r}"

    # It identifies the key without exposing it, which is what a record needs.
    assert secret.length == len(SECRET)
    assert re.fullmatch(r"[0-9a-f]{8}", secret.fingerprint)
    assert secret.hint.startswith(SECRET[:4]) and SECRET not in secret.hint
    # ...and there is exactly one way to get the value out.
    assert secret.reveal() == SECRET


# --- provider 1: the environment ---------------------------------------------


def test_env_provider_resolves_and_arms_the_redactor(monkeypatch):
    """The consumer role: resolution registers the value, so the belt is on
    before any caller can use it."""
    monkeypatch.setenv("BROKER_TEST_KEY", SECRET)
    secret = EnvSecretBroker().resolve(SecretRef.env("BROKER_TEST_KEY"))

    assert secret.reveal() == SECRET
    assert SECRET in registered_values()
    assert SECRET not in redact(f"leaked {SECRET}")


def test_env_provider_fails_closed_on_a_missing_or_empty_key(monkeypatch):
    monkeypatch.delenv("BROKER_TEST_KEY", raising=False)
    with pytest.raises(SecretUnavailable, match="is not set"):
        EnvSecretBroker().resolve(SecretRef.env("BROKER_TEST_KEY"))

    monkeypatch.setenv("BROKER_TEST_KEY", "   ")
    with pytest.raises(SecretUnavailable):
        EnvSecretBroker().resolve(SecretRef.env("BROKER_TEST_KEY"))


def test_env_provider_refuses_a_scheme_it_does_not_serve():
    with pytest.raises(SecretUnavailable, match="does not serve"):
        EnvSecretBroker().resolve(SecretRef(scheme="keychain", name="x"))


def test_env_provider_is_always_available_and_says_so():
    provider = EnvSecretBroker()
    assert provider.available() is True
    assert provider.unavailable_reason() is None


# --- provider 2: the macOS keychain ------------------------------------------


def _fake_security(returncode: int = 0, stdout: str = "", stderr: str = ""):
    def run(*args, **kwargs):
        return subprocess.CompletedProcess(args=args, returncode=returncode, stdout=stdout, stderr=stderr)

    return run


def test_keychain_provider_reports_an_honest_unavailable_reason(monkeypatch):
    monkeypatch.setattr("msb_v3.secrets.broker.shutil.which", lambda name: None)
    provider = KeychainSecretBroker()

    assert provider.available() is False
    assert provider.unavailable_reason() and "security" in provider.unavailable_reason()
    with pytest.raises(SecretUnavailable, match="security"):
        provider.resolve(SecretRef(scheme="keychain", name="msb-test"))


def test_keychain_provider_resolves_when_the_binary_is_present(monkeypatch):
    monkeypatch.setattr("msb_v3.secrets.broker.shutil.which", lambda name: "/usr/bin/security")
    monkeypatch.setattr("msb_v3.secrets.broker.subprocess.run", _fake_security(stdout=f"{SECRET}\n"))
    secret = KeychainSecretBroker().resolve(SecretRef(scheme="keychain", name="msb-test"))

    assert secret.reveal() == SECRET
    assert SECRET in registered_values()


def test_a_keychain_miss_does_not_smuggle_the_value_into_the_error(monkeypatch):
    monkeypatch.setattr("msb_v3.secrets.broker.shutil.which", lambda name: "/usr/bin/security")
    monkeypatch.setattr(
        "msb_v3.secrets.broker.subprocess.run",
        _fake_security(returncode=44, stderr=f"security: item not found for {SECRET}"),
    )
    with pytest.raises(SecretUnavailable) as excinfo:
        KeychainSecretBroker().resolve(SecretRef(scheme="keychain", name="msb-test"))

    # The stronger guarantee: the child's output is not relayed at all, so
    # there is nothing redaction has to catch in the first place.
    message = str(excinfo.value)
    assert SECRET not in message
    assert "exited 44" in message
    assert "item not found" not in message


def test_an_empty_keychain_item_is_unavailable_not_an_empty_secret(monkeypatch):
    monkeypatch.setattr("msb_v3.secrets.broker.shutil.which", lambda name: "/usr/bin/security")
    monkeypatch.setattr("msb_v3.secrets.broker.subprocess.run", _fake_security(stdout="\n"))
    with pytest.raises(SecretUnavailable, match="empty"):
        KeychainSecretBroker().resolve(SecretRef(scheme="keychain", name="msb-test"))


# --- registry: deterministic, fail-closed, tier-aware ------------------------


def test_registration_order_decides_and_the_first_eligible_provider_wins(monkeypatch):
    monkeypatch.setenv("BROKER_TEST_KEY", SECRET)
    stub = UnavailableSecretBroker()
    registry = SecretBrokerRegistry([stub, EnvSecretBroker()])

    # The stub is first and would serve the scheme, so it is tried; being
    # unavailable it is skipped rather than guessed at.
    assert registry.select(SecretRef.env("BROKER_TEST_KEY")) is registry.providers()[1]
    assert registry.resolve(SecretRef.env("BROKER_TEST_KEY")).reveal() == SECRET


def test_selection_is_fail_closed_and_aggregates_the_reasons():
    registry = SecretBrokerRegistry([UnavailableSecretBroker()])
    with pytest.raises(SecretUnavailable) as excinfo:
        registry.resolve(SecretRef.env("ANYTHING"))

    message = str(excinfo.value)
    assert "deliberately unavailable" in message
    assert str(SecretRef.env("ANYTHING")) in message


def test_selection_refuses_a_provider_above_the_callers_risk_budget(monkeypatch):
    """Resolving a credential is tier-4 work in this repo's table, so a caller
    with a tier-1 budget must be refused rather than quietly granted it."""
    monkeypatch.setenv("BROKER_TEST_KEY", SECRET)
    registry = SecretBrokerRegistry([EnvSecretBroker()])

    with pytest.raises(SecretUnavailable, match="budget"):
        registry.resolve(SecretRef.env("BROKER_TEST_KEY"), max_risk_tier=1)

    # ...and the same call succeeds when the budget is honest.
    assert registry.resolve(SecretRef.env("BROKER_TEST_KEY"), max_risk_tier=4).reveal() == SECRET


def test_the_default_registry_offers_both_real_stores():
    ids = [provider.spec.provider_id for provider in default_registry().providers()]
    assert ids == ["env", "keychain"]


# --- the swap test -----------------------------------------------------------


def test_swap_test_one_consumer_two_providers_no_consumer_change(monkeypatch):
    """The seam's required proof: the same consumer code resolves the same
    value from two different stores, with no edit to the consumer."""
    monkeypatch.setenv("BROKER_SWAP_KEY", SECRET)

    def consumer(registry: SecretBrokerRegistry, ref: SecretRef) -> str:
        # Consumer code as written: it names the Definition and never a
        # provider. The reference is data, so the same function can be pointed
        # at either store. Both calls below are this exact function.
        return registry.resolve(ref).reveal()

    from_env = SecretBrokerRegistry([EnvSecretBroker()])
    assert consumer(from_env, SecretRef.parse("secret://env/BROKER_SWAP_KEY")) == SECRET

    # Same consumer, different store: the keychain provider, via a fake binary.
    monkeypatch.setattr("msb_v3.secrets.broker.shutil.which", lambda name: "/usr/bin/security")
    monkeypatch.setattr("msb_v3.secrets.broker.subprocess.run", _fake_security(stdout=SECRET))
    from_keychain = SecretBrokerRegistry([KeychainSecretBroker()])
    assert consumer(from_keychain, SecretRef.parse("secret://keychain/msb-swap")) == SECRET


def test_swap_test_a_provider_can_be_replaced_by_availability_alone(monkeypatch):
    """Flipping which provider serves a reference is a registration change, not
    a code change — and the consumer cannot tell the difference."""
    monkeypatch.setenv("BROKER_SWAP_KEY", SECRET)
    ref = SecretRef.env("BROKER_SWAP_KEY")

    with_stub = SecretBrokerRegistry([UnavailableSecretBroker()])
    with_real = SecretBrokerRegistry([UnavailableSecretBroker(), EnvSecretBroker()])

    with pytest.raises(SecretUnavailable):
        with_stub.resolve(ref)
    assert with_real.resolve(ref) == with_real.resolve(ref)
    assert isinstance(with_real.resolve(ref), Secret)


# --- the belt is armed from configuration -------------------------------------


def test_seed_redaction_from_env_arms_configured_secrets_and_skips_the_rest(monkeypatch):
    monkeypatch.setenv("SEEDED_TEST_KEY", SECRET)
    seeded = seed_redaction_from_env(names=("SEEDED_TEST_KEY", "NOT_SET_ANYWHERE"))

    assert seeded == ("SEEDED_TEST_KEY",)
    assert SECRET in registered_values()
    assert SECRET not in redact(f"boom {SECRET}")


def test_seed_list_matches_the_drift_guard_so_it_cannot_rot(monkeypatch):
    """A secret missing from SEED_ENV_NAMES is a secret that can still be
    printed, so the list is pinned to the guard that defines the secret set."""
    guard = (REPO / "scripts" / "check-env-drift.sh").read_text(encoding="utf-8")
    guard_keys = guard.split("SECRET_KEYS=(", 1)[1].split(")", 1)[0].split()

    assert set(guard_keys) <= set(SEED_ENV_NAMES), (
        "every SECRET_KEYS entry in check-env-drift.sh must be seeded into the "
        f"redactor; missing: {sorted(set(guard_keys) - set(SEED_ENV_NAMES))}"
    )
