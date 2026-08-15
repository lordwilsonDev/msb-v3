#!/usr/bin/env python3
"""Signed-device CLI for the msb-v3 Vesta / Sovereign Node perimeter.

Enroll, open a signed session, and drive the signed chat / read / write /
shell flows from a terminal. See ``msb_v3.device.client`` for the full docs.

Usage (with .env loaded):
  python scripts/device-client.py enroll
  python scripts/device-client.py chat "hello"
  python scripts/device-client.py read runtime/node-sandbox/notes.md
  python scripts/device-client.py write runtime/node-sandbox/notes.md "hi"
  python scripts/device-client.py shell echo hello world
  python scripts/device-client.py status
"""

from __future__ import annotations

import sys

from msb_v3.device.client import main

if __name__ == "__main__":
    raise SystemExit(main())
