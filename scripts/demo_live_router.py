#!/usr/bin/env python3
"""Live test: the hybrid model router's frontier path, end to end.

Drives the real server (http://127.0.0.1:8766):

    1. POST /agent/handle with a PUBLIC plan (privacy=false)  -> plan +
       verify_synth route FRONTIER (R score > threshold, /v1 seam open)
    2. POST /agent/handle with a PRIVATE plan (default)       -> plan is
       forced LOCAL by the privacy floor
    3. Scrape /metrics/prometheus and show the router decision
       counter (`msb_v3_router_decisions_total`) that landed in the
       server's Prometheus registry — the Phase 2 live acceptance.

Requires: the server running the current code, MSB_OPERATOR_TOKEN in
.env, OPENAI_API_KEY set (open seam). Operator-gated: the /agent surface
is fail-closed (503) without the token.

Usage: python scripts/demo_live_router.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

BASE = "http://127.0.0.1:8766"
OUT = Path.home() / "Desktop" / "out"


def _sh(*args: str, timeout: int = 300) -> str:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout).stdout


def _metrics() -> str:
    # The endpoint returns a JSON-escaped string (literal \n inside one
    # line); decode it so line-based matching works.
    raw = _sh("curl", "-s", "-m", "5", f"{BASE}/metrics/prometheus")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _router_samples() -> list[str]:
    return [l for l in _metrics().splitlines() if l.startswith("msb_v3_router_decisions_total")]


def _handle(request: str, privacy: bool | None, token: str, *, approve: bool = False) -> dict:
    body: dict = {"request": request}
    if privacy is not None:
        body["privacy"] = privacy
    if approve:
        body["approve"] = True
    out = _sh(
        "curl", "-s", "-m", "240",
        "-H", f"Authorization: Bearer {token}",
        "-H", "Content-Type: application/json",
        "-d", json.dumps(body),
        f"{BASE}/agent/handle",
    )
    d = json.loads(out)
    # A FAIL/ERROR run returns HTTP 500 with the payload under "detail".
    if "detail" in d and isinstance(d.get("detail"), dict):
        d = d["detail"]
    return d


def _token_from_env() -> str:
    env = os.getenv("MSB_OPERATOR_TOKEN")
    if env:
        return env
    # scripts/ -> repo root
    env_file = Path(__file__).resolve().parents[1] / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("MSB_OPERATOR_TOKEN="):
                return line.split("=", 1)[1]
    return ""


def main() -> int:
    token = _token_from_env()
    if not token:
        print("FAIL: MSB_OPERATOR_TOKEN not found — the /agent surface is closed")
        return 1

    print("=== baseline router decisions ===")
    base = _router_samples()
    print("\n".join(base) or "(none yet — clean server)")

    print("\n=== RUN 1: PUBLIC plan (privacy=false -> frontier) ===")
    # Explicit write request + operator approval: the cloud plan (DeepSeek)
    # may invent a write task, and the A8 taint gate must then allow it
    # because it was pre-approved — proving the full loop completes.
    r1 = _handle("public: write a summary file about the sovereign agentic runtime", False, token, approve=True)
    print(f"verdict={r1.get('verdict')} run_id={r1.get('run_id')} hash={str(r1.get('deterministic_hash'))[:12]} error={r1.get('error')}")

    print("\n=== RUN 2: PRIVATE plan (default -> privacy floor forces local) ===")
    r2 = _handle("private: summarize the sovereign agentic runtime from the vault", None, token)
    print(f"verdict={r2.get('verdict')} run_id={r2.get('run_id')} hash={str(r2.get('deterministic_hash'))[:12]} error={r2.get('error')}")

    print("\n=== router decisions in server /metrics/prometheus ===")
    samples = _router_samples()
    for s in samples:
        print(" ", s)
    if not samples:
        print("  FAIL: no router decision samples scraped")
        return 1

    frontier_plan = any('task_kind="plan"' in s and 'tier="frontier"' in s for s in samples)
    privacy_local = any('cause="privacy"' in s and 'tier="local"' in s for s in samples)
    ok = frontier_plan and privacy_local
    print(f"\nRESULT: {'PASS' if ok else 'FAIL'}")
    print("  public plan  -> frontier decision in Prometheus:", frontier_plan)
    print("  private plan -> local (privacy floor) decision in Prometheus:", privacy_local)
    # Show the public run's task outcomes so a GateReview (safety gate
    # blocking an unapproved write) is visible evidence, not a mystery.
    trace = r1.get("trace") or {}
    if trace:
        print("  public run graph_source:", trace.get("graph_source"))
        for e in (trace.get("execution") or []):
            v = e.get("verification") or {}
            print(
                "   ", e.get("task_id"), "|", "ok" if e.get("ok") else "blocked",
                "|", v.get("check") or "", v.get("verdict") or "",
                "|", str(v.get("detail") or e.get("error") or "")[:50],
            )

    OUT.mkdir(parents=True, exist_ok=True)
    stamp = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = OUT / f"dbb-router-{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "public.json").write_text(json.dumps(r1, indent=2))
    (out_dir / "private.json").write_text(json.dumps(r2, indent=2))
    (out_dir / "router_metrics.txt").write_text("\n".join(samples))
    print(f"\nevidence: {out_dir}/")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
