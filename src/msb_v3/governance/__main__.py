"""Entry point: python -m msb_v3.governance ... (mirrors msb_v3.ops)."""

from __future__ import annotations

import sys

from msb_v3.governance.cli import main

if __name__ == "__main__":
    sys.exit(main())
