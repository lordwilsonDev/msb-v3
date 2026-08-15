"""Entry point: python -m msb_v3.flywheel ...

Bootstraps the repo env before any msb_v3 import: loads the chain-anchor
key from .env when unset (the audit chain fails closed on keyless appends
now) and pins MSB_DB_PATH to the repo so the chain/turn DBs resolve here
regardless of the caller's CWD (a bare relative default would scatter
DBs under whatever directory the CLI is run from).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]


def _bootstrap_env() -> None:
    if not os.getenv("MSB_CHAIN_ANCHOR_KEY"):
        for env_file in (_REPO / ".env", Path.home() / "msb-v3" / ".env"):
            if not env_file.exists():
                continue
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line.startswith("MSB_CHAIN_ANCHOR_KEY="):
                    os.environ["MSB_CHAIN_ANCHOR_KEY"] = line.split("=", 1)[1].strip().strip("\"'")
                    break
            if os.getenv("MSB_CHAIN_ANCHOR_KEY"):
                break
    os.environ.setdefault("MSB_DB_PATH", str(_REPO / "data" / "msb_v3.db"))


_bootstrap_env()

from msb_v3.flywheel.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
