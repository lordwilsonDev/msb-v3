"""SecretBroker — the capability seam over *where a secret actually lives*.

The rule this exists to enforce: **a model context may carry a reference, never
a resolved value.** A reference (``secret://env/OPENAI_API_KEY``) is safe to
log, embed in a prompt, or reason about; ``Secret.reveal()`` is the only way to
obtain the raw value, and it is deliberately one greppable accessor so a review
can find every place a real credential is unwrapped.

The four roles of a seam (see the interchangeable-components pattern):

| Role | Here |
|---|---|
| **Service Definition** | ``SecretBroker`` — consumers program against this, never against a provider |
| **Service Provider** | ``EnvSecretBroker``, ``KeychainSecretBroker``, ``UnavailableSecretBroker`` |
| **Consumer** | the redactor: ``resolve()`` registers every value it hands out, so the belt is on before the value is used |
| **Registry** | ``SecretBrokerRegistry`` — deterministic registration order, fail-closed, tier-aware |

Why a seam rather than one class: the second provider already exists in fact.
The anchor seed lives in the macOS login keychain (``scripts/store-anchor-key.sh``
is the precedent), while provider keys live in the environment — two real
stores with the same consumer contract. ``UnavailableSecretBroker`` makes a swap
observable in tests without either store being present.

**Risk tier.** Resolving a secret is honest-tier-4 work in this repo's table
(``1`` read / ``2`` write_file / ``3`` delete-send / ``4`` financial-permissions
in ``msb_v3.agent.safety.RISK_TIERS``): the thing being handed back *is* a
credential, which is closer to ``permissions`` than to ``read_vault``. Both
providers declare tier 4, and ``select()`` refuses to exceed a caller's budget.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterable

from msb_v3.secrets.redact import register_secret_value

# Names seeded into the redactor at process start so the value-based half of
# redaction bites even for secrets nothing has resolved yet this run. Kept in
# sync with check-env-drift.sh's SECRET_KEYS list by a test, because a secret
# missing from here is a secret that can be printed.
SEED_ENV_NAMES: tuple[str, ...] = (
    "OPENAI_API_KEY",
    "MSB_OPERATOR_TOKEN",
    "WEBUI_SECRET_KEY",
    "TENCENT_COS_SECRET_ID",
    "TENCENT_COS_SECRET_KEY",
    "MSB_ALERT_EMAIL",
    "MSB_TELEGRAM_BOT_TOKEN",
    "MSB_TELEGRAM_CHAT_ID",
    "N8N_API_KEY",
    "MSB_ZAPIER_API_KEY",
    "MSB_GHL_API_KEY",
    "TYPESAFE_API_KEY",
    "MCP_BRIDGE_SECRET",
    "OBSIDIAN_API_KEY",
)


class SecretUnavailable(RuntimeError):
    """A reference could not be resolved. Always fail-closed, never guessed."""


@dataclass(frozen=True)
class SecretRef:
    """A *pointer* to a secret. Safe to log, prompt with, and store."""

    scheme: str
    name: str

    @classmethod
    def parse(cls, raw: str) -> "SecretRef":
        if not raw.startswith("secret://"):
            raise ValueError(f"not a secret reference: {raw!r}")
        rest = raw[len("secret://") :]
        scheme, _, name = rest.partition("/")
        if not scheme or not name:
            raise ValueError(f"secret reference needs a scheme and a name: {raw!r}")
        return cls(scheme=scheme, name=name)

    @classmethod
    def env(cls, name: str) -> "SecretRef":
        return cls(scheme="env", name=name)

    def __str__(self) -> str:
        return f"secret://{self.scheme}/{self.name}"


@dataclass(frozen=True)
class Secret:
    """A resolved value that resists being printed by accident.

    ``str()``/``repr()``/f-string interpolation all produce a masked form, so
    logging or echoing a ``Secret`` cannot leak it. ``reveal()`` is the only
    accessor that returns the raw value.
    """

    ref: SecretRef
    value: str

    def reveal(self) -> str:
        return self.value

    @property
    def length(self) -> int:
        return len(self.value)

    @property
    def fingerprint(self) -> str:
        """Stable, non-reversible tag for correlating a key across records."""
        return hashlib.sha256(self.value.encode("utf-8")).hexdigest()[:8]

    @property
    def hint(self) -> str:
        """First characters only — enough to tell two keys apart, not to use one."""
        return f"{self.value[:4]}…"

    def __str__(self) -> str:
        return f"<secret {self.ref} len={self.length} fp={self.fingerprint}>"

    def __repr__(self) -> str:
        return self.__str__()


@dataclass(frozen=True)
class BrokerSpec:
    """What a provider is, in the registry's terms. Same shape for every provider."""

    provider_id: str
    kind: str
    schemes: frozenset[str]
    max_risk_tier: int
    timeout_s: float
    description: str = ""


class SecretBroker(ABC):
    """Service Definition. Consumers import this, never a provider."""

    spec: BrokerSpec

    @abstractmethod
    def resolve(self, ref: SecretRef) -> Secret:
        """Return the resolved secret, or raise ``SecretUnavailable``."""

    @abstractmethod
    def available(self) -> bool:
        """Whether this provider can serve at all right now."""

    @abstractmethod
    def unavailable_reason(self) -> str | None:
        """Why not — a non-empty reason when ``available()`` is False."""


class EnvSecretBroker(SecretBroker):
    """Provider #1 — the process environment (where provider keys already live)."""

    spec = BrokerSpec(
        provider_id="env",
        kind="environment",
        schemes=frozenset({"env"}),
        max_risk_tier=4,
        timeout_s=0.1,
        description="process environment; always available in-process",
    )

    def resolve(self, ref: SecretRef) -> Secret:
        if ref.scheme != "env":
            raise SecretUnavailable(f"env provider does not serve {ref.scheme!r}")
        raw = os.environ.get(ref.name)
        if raw is None or not raw.strip():
            raise SecretUnavailable(f"{ref} is not set in the environment")
        secret = Secret(ref=ref, value=raw.strip())
        # The belt goes on here, at the moment of resolution, so no consumer has
        # to remember to do it.
        register_secret_value(secret.value)
        return secret

    def available(self) -> bool:
        return True

    def unavailable_reason(self) -> str | None:
        return None


class KeychainSecretBroker(SecretBroker):
    """Provider #2 — the macOS login keychain.

    Same store ``scripts/store-anchor-key.sh`` uses for the anchor seed. The
    value is read with ``security find-generic-password -w`` and never logged,
    never echoed, and never put on a command line (``security`` takes it as an
    argument list, not a shell string).

    On a miss the error names the exit code and **not** the child's stderr. That
    is deliberate and was found by a test: a subprocess's free-text output can
    quote a value this process has never seen, and redaction cannot mask what it
    has never seen — so arbitrary child output is not relayed at all. (The first
    version of this method interpolated stderr, and the exposure test that was
    supposed to prove the miss was clean failed on exactly that string.)
    """

    spec = BrokerSpec(
        provider_id="keychain",
        kind="os-keychain",
        schemes=frozenset({"keychain"}),
        max_risk_tier=4,
        timeout_s=5.0,
        description="macOS login keychain via the security(1) binary",
    )

    def available(self) -> bool:
        return shutil.which("security") is not None

    def unavailable_reason(self) -> str | None:
        if not self.available():
            return "the security(1) binary is not on PATH (non-macOS host?)"
        return None

    def resolve(self, ref: SecretRef) -> Secret:
        if ref.scheme != "keychain":
            raise SecretUnavailable(f"keychain provider does not serve {ref.scheme!r}")
        if not self.available():
            raise SecretUnavailable(f"{ref}: {self.unavailable_reason()}")
        proc = subprocess.run(
            ["security", "find-generic-password", "-s", ref.name, "-w"],
            capture_output=True,
            text=True,
            timeout=self.spec.timeout_s,
            check=False,
        )
        if proc.returncode != 0:
            # Exit code only — see the class docstring: a child's stderr is not
            # relayed, because its content is outside anything redaction can
            # know about.
            raise SecretUnavailable(
                f"{ref} not found in the keychain (security(1) exited {proc.returncode})"
            )
        value = proc.stdout.strip()
        if not value:
            raise SecretUnavailable(f"{ref} is present in the keychain but empty")
        secret = Secret(ref=ref, value=value)
        register_secret_value(secret.value)
        return secret


class UnavailableSecretBroker(SecretBroker):
    """A provider that is never available — it makes a swap observable.

    Present so a test can prove the registry *notices* an unavailable provider
    and fails closed with a reason, without needing a broken real store.
    """

    spec = BrokerSpec(
        provider_id="unavailable",
        kind="stub",
        schemes=frozenset({"env", "keychain"}),
        max_risk_tier=4,
        timeout_s=0.0,
        description="never available; used to test fail-closed selection",
    )

    def resolve(self, ref: SecretRef) -> Secret:
        raise SecretUnavailable(f"{ref}: {self.unavailable_reason()}")

    def available(self) -> bool:
        return False

    def unavailable_reason(self) -> str | None:
        return "stub provider: deliberately unavailable"


class SecretBrokerRegistry:
    """Deterministic, fail-closed selection among providers.

    Order of registration decides; there is no scoring and no model input. A
    provider is eligible only if it serves the reference's scheme, is
    ``available()``, and its ``max_risk_tier`` fits the caller's budget.
    """

    def __init__(self, providers: Iterable[SecretBroker] = ()) -> None:
        self._providers: list[SecretBroker] = list(providers)

    def register(self, provider: SecretBroker) -> None:
        self._providers.append(provider)

    def providers(self) -> tuple[SecretBroker, ...]:
        return tuple(self._providers)

    def select(self, ref: SecretRef, *, max_risk_tier: int = 4) -> SecretBroker:
        reasons: list[str] = []
        for provider in self._providers:
            if ref.scheme not in provider.spec.schemes:
                continue
            if not provider.available():
                reasons.append(
                    f"{provider.spec.provider_id}: {provider.unavailable_reason() or 'unavailable'}"
                )
                continue
            if provider.spec.max_risk_tier > max_risk_tier:
                reasons.append(
                    f"{provider.spec.provider_id}: tier {provider.spec.max_risk_tier} "
                    f"exceeds the caller's budget {max_risk_tier}"
                )
                continue
            return provider
        detail = "; ".join(reasons) if reasons else "no provider serves this scheme"
        raise SecretUnavailable(f"cannot resolve {ref} — {detail}")

    def resolve(self, ref: SecretRef, *, max_risk_tier: int = 4) -> Secret:
        return self.select(ref, max_risk_tier=max_risk_tier).resolve(ref)


def default_registry() -> SecretBrokerRegistry:
    """A registry with the real providers registered, in preference order."""
    return SecretBrokerRegistry([EnvSecretBroker(), KeychainSecretBroker()])


def seed_redaction_from_env(
    registry: SecretBrokerRegistry | None = None,
    names: Iterable[str] = SEED_ENV_NAMES,
) -> tuple[str, ...]:
    """Register the configured secrets as redaction targets.

    Returns the names that resolved. Missing names are skipped rather than
    raising: an unset key means "nothing to mask", not "unsafe state" — the
    fail-closed rule applies to *resolving* a secret, not to arming the belt.
    """
    active = registry if registry is not None else default_registry()
    seeded: list[str] = []
    for name in names:
        try:
            active.resolve(SecretRef.env(name))
        except SecretUnavailable:
            continue
        seeded.append(name)
    return tuple(seeded)
