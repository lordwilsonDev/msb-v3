#!/usr/bin/env python3
"""Run the hardware-independent Vesta signed-device loopback probe."""

from __future__ import annotations

import argparse
import json
import os
import sys

from msb_v3.vesta.dev_harness import LoopbackDevice


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default=os.getenv("VESTA_LOOPBACK_URL", "http://127.0.0.1:8766"),
        help="MSB base URL (default: VESTA_LOOPBACK_URL or loopback)",
    )
    parser.add_argument(
        "--query",
        default="Reply with exactly LOOPBACK_OK.",
        help="bounded chat prompt used for the probe",
    )
    parser.add_argument(
        "--device-id",
        default=None,
        help="optional enrolled device id; omit to create an ephemeral id",
    )
    parser.add_argument(
        "--pairing-code-stdin",
        action="store_true",
        help="read the pairing code from stdin instead of MSB_NODE_PAIRING_CODE",
    )
    args = parser.parse_args()

    pairing_code = sys.stdin.readline().rstrip("\n") if args.pairing_code_stdin else os.getenv("MSB_NODE_PAIRING_CODE", "")
    if not pairing_code:
        print("MSB_NODE_PAIRING_CODE is empty; enrollment is intentionally closed", file=sys.stderr)
        return 2

    kwargs = {"base_url": args.url}
    if args.device_id:
        kwargs["device_id"] = args.device_id
    try:
        with LoopbackDevice(**kwargs) as device:
            result = device.probe(pairing_code, args.query)
    except Exception as exc:
        print(f"loopback probe failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
