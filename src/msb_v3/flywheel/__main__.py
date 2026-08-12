"""Entry point: python -m msb_v3.flywheel ..."""

from __future__ import annotations

import sys

from msb_v3.flywheel.cli import main

if __name__ == "__main__":
    sys.exit(main())
