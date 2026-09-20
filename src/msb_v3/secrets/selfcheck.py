"""Startup self-check — refuse to run with the redactor dark.

The failure this exists for is not a crash. It is a process that starts
normally, serves traffic, and **masks nothing** because its environment was not
the configured one. JOB-026 armed the redactor at startup and logged the count
at INFO, which is exactly the shape of a silent failure: `seeded: 0` scrolls
past, and the first evidence of a problem is a credential sitting in a log file.

## What counts as "should have been armed"

Arming depends entirely on what is in the process environment: `resolve()` reads
`os.environ`, so `armed == 0` means *this process has none of the configured
secrets in its environment*. From inside the process that state cannot be
distinguished from "no secrets are configured here" — a fresh clone and a
misconfigured service look identical. Two pieces of evidence settle it:

1. ``MSB_REQUIRE_SECRET_REDACTION=1`` — set by the entrypoint that loads the
   secrets (`scripts/run.sh` sets it, so the requirement travels with the code
   that does the loading rather than with the `.env` that failed to load).
2. **A `.env` file exists at ``settings.msb_home``** — the machine is configured
   and this process is not, which is precisely the mismatch worth failing on.

## Verdicts

| verdict | meaning | behaviour |
|---|---|---|
| ``armed`` | at least one configured secret is registered | INFO, gauge set |
| ``unarmed-expected`` | configured instance, nothing armed | **raise** — the app refuses to start |
| ``unarmed-overridden`` | as above, but ``MSB_ALLOW_UNARMED_REDACTION=1`` | WARNING, starts anyway |
| ``unarmed-unconfigured`` | nothing configured, nothing expected (fresh clone, CI) | WARNING, starts |
| ``unknown`` | the check itself failed | WARNING, starts |

The override is deliberate and tested: the *test suite* sets it (94 test modules
build the app, and pytest runs without `.env` exported), so the guard protects
the service rather than fighting the suite. It is never silent — an overridden
start raises a WARNING that names the flag, and the gauge reports ``0``.

`redaction_self_check` never raises: a check that throws would take startup down
for a reason that is not the thing it is checking. The *caller* raises, on the
verdict, which is what makes the failure loud and located.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from msb_v3.secrets.broker import SEED_ENV_NAMES, seed_redaction_from_env
from msb_v3.secrets.redact import registered_values

EXPECT_ENV_FLAG = "MSB_REQUIRE_SECRET_REDACTION"
ALLOW_ENV_FLAG = "MSB_ALLOW_UNARMED_REDACTION"

VERDICT_ARMED = "armed"
VERDICT_EXPECTED = "unarmed-expected"
VERDICT_OVERRIDDEN = "unarmed-overridden"
VERDICT_UNCONFIGURED = "unarmed-unconfigured"
VERDICT_UNKNOWN = "unknown"


class SecretRedactionUnarmed(RuntimeError):
    """Raised by the app when a configured instance would mask nothing."""


@dataclass(frozen=True)
class RedactionStatus:
    """The outcome of the startup self-check. Plain data, so it can be logged,
    asserted on and reported without touching the registry again."""

    verdict: str
    armed: int
    seeded: tuple[str, ...]
    expected: bool
    evidence: str
    reason: str

    @property
    def ok(self) -> bool:
        """False only for the one verdict that is a real failure."""
        return self.verdict != VERDICT_EXPECTED

    def as_dict(self) -> dict[str, object]:
        return {
            "verdict": self.verdict,
            "armed": self.armed,
            "seeded": list(self.seeded),
            "expected": self.expected,
            "ok": self.ok,
            "evidence": self.evidence,
            "reason": self.reason,
        }


def _flagger(name: str) -> bool:
    return os.environ.get(name, "").strip() == "1"


def _env_file_path() -> Path:
    # Imported lazily: this module is reachable from msb_v3.secrets, which
    # core.logging imports, and core.config must not be pulled in at import time
    # where that could form a cycle.
    from msb_v3.core.config import settings

    return Path(settings.msb_home) / ".env"


def redaction_self_check(
    names: tuple[str, ...] = SEED_ENV_NAMES,
    *,
    env_file: Path | None = None,
) -> RedactionStatus:
    """Arm the redactor for the configured secrets, then judge the result.

    It arms as well as checks on purpose: a check that only read the registry
    would report ``0`` on every fresh process and tell us nothing.
    """
    try:
        seeded = seed_redaction_from_env(names=names)
        armed = len(registered_values())

        forced = _flagger(EXPECT_ENV_FLAG)
        path = env_file if env_file is not None else _env_file_path()
        env_file_present = path.is_file()
        expected = forced or env_file_present

        if forced:
            evidence = f"{EXPECT_ENV_FLAG}=1"
        elif env_file_present:
            evidence = f"{path} exists"
        else:
            evidence = f"no {EXPECT_ENV_FLAG} and no {path}"

        if armed:
            return RedactionStatus(
                verdict=VERDICT_ARMED,
                armed=armed,
                seeded=seeded,
                expected=expected,
                evidence=evidence,
                reason=f"{armed} secret value(s) registered for masking",
            )

        if not expected:
            return RedactionStatus(
                verdict=VERDICT_UNCONFIGURED,
                armed=0,
                seeded=seeded,
                expected=False,
                evidence=evidence,
                reason=(
                    "no configured secrets in this environment and none expected "
                    "(fresh clone / CI): masking is inactive, which hides nothing "
                    "because there is nothing to hide"
                ),
            )

        if _flagger(ALLOW_ENV_FLAG):
            return RedactionStatus(
                verdict=VERDICT_OVERRIDDEN,
                armed=0,
                seeded=seeded,
                expected=True,
                evidence=evidence,
                reason=(
                    f"a configured instance would mask nothing, but "
                    f"{ALLOW_ENV_FLAG}=1 was set: starting with redaction inactive"
                ),
            )

        return RedactionStatus(
            verdict=VERDICT_EXPECTED,
            armed=0,
            seeded=seeded,
            expected=True,
            evidence=evidence,
            reason=(
                f"this process looks configured for secrets ({evidence}) but none of "
                f"the {len(names)} configured names resolved, so the redactor would "
                f"mask nothing while the app runs. Fix: start the process so it loads "
                f"its environment (scripts/run.sh sources the repo .env), or set "
                f"{ALLOW_ENV_FLAG}=1 to accept an unarmed start deliberately."
            ),
        )
    except Exception as exc:  # noqa: BLE001 — the check must never break startup
        return RedactionStatus(
            verdict=VERDICT_UNKNOWN,
            armed=0,
            seeded=(),
            expected=False,
            evidence="check failed",
            reason=f"redaction self-check could not complete: {type(exc).__name__}: {exc}",
        )
