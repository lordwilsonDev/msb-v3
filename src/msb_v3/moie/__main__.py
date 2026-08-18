"""Entry point: python -m msb_v3.moie policy [--policy PATH] [--json] [--strict]

Validates the detection policy (config/risk_templates.json) with the same
fail-closed loader the engine uses, then diffs its detection coverage
against the frozen gate corpus. No environment bootstrap needed — pure
deterministic policy work, no DBs, no model calls.
"""

from __future__ import annotations

import sys

from msb_v3.moie.cli import main

if __name__ == "__main__":
    sys.exit(main())
