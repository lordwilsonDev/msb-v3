#!/usr/bin/env python3
"""Rotate secrets in .env and restart dependent services."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Repo root: MSB_HOME / MSB_REPO env, else derived from this script's
# location (scripts/ -> parents[1] = repo root).
_REPO = Path(os.getenv("MSB_HOME") or os.getenv("MSB_REPO") or str(Path(__file__).resolve().parents[1]))

ENV_PATH = _REPO / ".env"
ROTATION_LOG = _REPO / "runtime" / "secret-rotation.log"
ROTATION_LOG.parent.mkdir(parents=True, exist_ok=True)

_SECRETS = {
    "MCP_BRIDGE_SECRET": 32,
    "OBSIDIAN_API_KEY": 64,
}


def _hex(n: int) -> str:
    return os.urandom(n).hex()


def _rotate() -> list[dict[str, str]]:
    if not ENV_PATH.exists():
        raise FileNotFoundError(f".env not found at {ENV_PATH}")

    content = ENV_PATH.read_text(encoding="utf-8")
    rotated = []
    for key, length in _SECRETS.items():
        new_val = _hex(length)
        pattern = re.compile(rf"^{key}=.*$", re.MULTILINE)
        if pattern.search(content):
            content = pattern.sub(f"{key}={new_val}", content)
        else:
            content += f"\n{key}={new_val}\n"
        rotated.append({"key": key, "value": new_val})

    ENV_PATH.write_text(content, encoding="utf-8")
    ts = datetime.now(timezone.utc).isoformat()
    with open(ROTATION_LOG, "a", encoding="utf-8") as fh:
        fh.write(f"[{ts}] rotated: {', '.join(r['key'] for r in rotated)}\n")
    return rotated


def _restart_msb() -> None:
    """Restart only through the owned launchd/service control surface.

    This script must never discover a listener by port and terminate it: the
    process may belong to another checkout or operator. Set
    ``MSB_RESTART_COMMAND`` to an explicit, deployment-owned command when
    rotation needs an immediate restart; otherwise leave restart to the
    supervisor and report that fact.
    """
    command = os.environ.get("MSB_RESTART_COMMAND", "").strip()
    if not command:
        print(
            "warning: secrets rotated; no owned restart command configured; "
            "restart msb-v3 through its supervisor",
            file=sys.stderr,
        )
        return
    subprocess.run(["bash", "-lc", command], check=True)


def main() -> int:
    try:
        rotated = _rotate()
        print("rotated secrets:")
        for r in rotated:
            print(f"  {r['key']}={r['value']}")
        _restart_msb()
        print("msb-v3 restart initiated")
        return 0
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
