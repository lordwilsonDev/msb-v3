#!/usr/bin/env python3
"""Rotate the secrets this box OWNS in .env.

    python scripts/rotate_secrets.py --dry-run   # report, change nothing
    python scripts/rotate_secrets.py --yes       # rotate

Rotation generates replacement values locally, which is correct only for
secrets this machine owns. Third-party credentials (TYPESAFE_API_KEY and the
rest of the provider keys) are NOT in scope: their rotation is re-issuing at
the vendor, then re-storing (see scripts/set-typesafe-key.sh for that one).
"""

from __future__ import annotations

import argparse
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

# Every entry here is a secret this box OWNS and can therefore regenerate.
# Rotating a THIRD-PARTY key through this dict would overwrite a working
# credential with random hex, so vendor keys (TYPESAFE_API_KEY and the other
# provider keys in .env.example) must NEVER be added: their rotation is
# re-issuing at the vendor, then re-storing (scripts/set-typesafe-key.sh for
# that one). The distinction is the whole reason this dict is short.
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


def _present(key: str) -> bool:
    """Is this key set in .env? Reports presence only, never the value."""
    if not ENV_PATH.exists():
        return False
    pattern = re.compile(rf"^{key}=.+$", re.MULTILINE)
    return bool(pattern.search(ENV_PATH.read_text(encoding="utf-8")))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rotate_secrets.py",
        description=(
            "Rotate the secrets this box OWNS in .env. Values are generated "
            "locally, so any third-party key in .env is out of scope: rotating "
            "one here would overwrite a working credential with random hex. "
            "Re-issue vendor keys at the vendor and re-store them."
        ),
    )
    parser.add_argument("--dry-run", action="store_true", help="report what would rotate; write nothing")
    parser.add_argument("--yes", action="store_true", help="confirm the rotation (required)")
    args = parser.parse_args(argv)

    # 2026-09-20: this script parsed no arguments at all, so `--help` ROTATED
    # both live secrets and overwrote them in .env — an unrecognised flag was
    # read as consent. It was recovered from the environment of the still-
    # running service, but only by luck. The destructive path now needs an
    # explicit --yes, which means no flag, a typo, or --help can reach it.
    # Same lesson as the listener-killing removed in the 2026-08-27 audit: the
    # default must be the safe branch.
    if not args.yes and not args.dry_run:
        print("refusing to rotate: pass --yes to confirm (nothing was changed)", file=sys.stderr)
        print("  python scripts/rotate_secrets.py --dry-run   # show what would rotate", file=sys.stderr)
        print("  python scripts/rotate_secrets.py --yes       # rotate", file=sys.stderr)
        return 2

    if args.dry_run:
        print("would rotate (values withheld):")
        for key in _SECRETS:
            print(f"  {key}: {'set in .env, would be replaced' if _present(key) else 'absent, would be added'}")
        print(f"target file: {ENV_PATH}")
        print(f"rotation log: {ROTATION_LOG}")
        if not os.environ.get("MSB_RESTART_COMMAND", "").strip():
            print("restart: no MSB_RESTART_COMMAND configured — restart via the supervisor yourself")
        return 0

    try:
        rotated = _rotate()
        print("rotated secrets:")
        for r in rotated:
            print(f"  {r['key']}={r['value']}")
        print("note: every client holding the previous value must be updated")
        _restart_msb()
        print("msb-v3 restart initiated")
        return 0
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
