"""Injectable configuration for the msb_ledger standalone library.

The ledger's only coupling to the host application was ``msb_v3.core.config``
(5 sites: audit_chain, chain_anchor, observer_log, axiom_library,
research_backend). This module severs it: ``LedgerConfig`` carries the exact
four values those modules read, resolved env-first with the same defaults the
app uses, so production behavior is unchanged with zero wiring. Host
applications that want explicit injection (tests, a container, a different
host) call ``configure(...)`` before importing the ledger modules.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Repo root: MSB_HOME env wins, then MSB_REPO (CI sets this), then derived
# from this file's location (src/msb_ledger/config.py -> parents[2] = repo
# root). Mirrors msb_v3.core.config semantics so defaults match production.
_REPO_ROOT = Path(
    os.getenv("MSB_HOME") or os.getenv("MSB_REPO") or str(Path(__file__).resolve().parents[2])
)


@dataclass
class LedgerConfig:
    """The four host-settings the ledger modules consumed. Env-first defaults
    identical to msb_v3.core.config so the standalone library behaves the
    same as when it lived inside the app."""

    db_path: str = field(default_factory=lambda: os.getenv("MSB_DB_PATH") or str(_REPO_ROOT / "data" / "msb_v3.db"))
    msb_home: str = field(default_factory=lambda: str(_REPO_ROOT))
    operator_token: str = field(default_factory=lambda: os.getenv("MSB_OPERATOR_TOKEN", ""))
    tavily_api_key: str = field(default_factory=lambda: os.getenv("TAVILY_API_KEY", ""))


_ledger_config = LedgerConfig()


def configure(
    *,
    db_path: str | None = None,
    msb_home: str | None = None,
    operator_token: str | None = None,
    tavily_api_key: str | None = None,
) -> None:
    """Explicitly inject host settings before the ledger modules are used.

    Mutates the shared ``settings`` object in place (the ledger modules hold
    ``from msb_ledger.config import settings``, so rebinding here would not
    reach them). Pass None to keep a field's current value.
    """
    if db_path is not None:
        settings.db_path = db_path
    if msb_home is not None:
        settings.msb_home = msb_home
    if operator_token is not None:
        settings.operator_token = operator_token
    if tavily_api_key is not None:
        settings.tavily_api_key = tavily_api_key


# The module-level settings object the ledger modules import — mirrors the
# `settings` name they used before, so the extraction is a 1-line import swap.
settings = _ledger_config
