#!/usr/bin/env python3
"""r01_retrieval_router_runner.py — Semantic Retrieval Router regression gate.

Re-runs the offline router test suite (tests/api/test_retrieval_router.py):
deterministic planner, weighted RRF fusion, parallel dispatch with fake
adapters, provenance assembly, graceful route failure, and the /smi/query
endpoint. Zero-spend by construction — fake adapters, no Qdrant/Ollama/LLM,
no network. This is the "verified not assumed" guard for the P0 router.

Artifact: <repo>/artifacts/hygiene/r01_retrieval_router_<ts>.json
Exit code: 0 = pass, 1 = fail.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(os.environ.get("MSB_REPO", Path(__file__).resolve().parents[2]))
PY = os.environ.get("MSB_PYTHON", sys.executable)
EVIDENCE_DIR = REPO / "artifacts" / "hygiene"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
SKILL = "regression-hygiene"
TARGET = REPO / "tests" / "api" / "test_retrieval_router.py"


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def main() -> int:
    started = time.perf_counter()
    out = EVIDENCE_DIR / f"r01_retrieval_router_{_now()}.json"
    if not TARGET.exists():
        artifact = {
            "experiment": "r01_retrieval_router",
            "experiment_id": "r01_retrieval_router",
            "artifact": str(out),
            "skill": SKILL,
            "input": f"pytest {TARGET}",
            "environment": f"msb-v3 repo @ {REPO}",
            "expected_behavior": "all offline retrieval-router tests pass (planner, RRF, dispatch, provenance, endpoint)",
            "actual_behavior": f"test file not found: {TARGET}",
            "latency_ms": 0,
            "errors": [f"missing {TARGET}"],
            "state_before": {"target": str(TARGET)},
            "state_after": {"passed": False, "summary": "test file missing"},
            "recovery": "ensure tests/api/test_retrieval_router.py exists in the tree",
            "false_repair": False,
            "evidence": [f"missing {TARGET}"],
            "verdict": "fail",
        }
        out.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
        print(json.dumps(artifact, indent=2))
        return 1

    attempts = []
    for attempt in (1, 2):
        try:
            proc = subprocess.run(
                [PY, "-m", "pytest", str(TARGET), "-q"],
                capture_output=True, text=True, timeout=600, check=False,
            )
        except subprocess.TimeoutExpired:
            proc = type("P", (), {"returncode": 124, "stdout": "", "stderr": "timed out after 600s"})()
        combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
        attempts.append({"attempt": attempt, "exit": proc.returncode,
                         "stderr_tail": (proc.stderr or "")[-300:]})
        if proc.returncode == 0:
            break
        # Infra-flake signature: pytest died before reporting any result
        # (exit 2 with no outcome line, e.g. startup crash under suite load —
        # seen 2026-08-14: banner printed, then exit 2 with no "N passed").
        # A real failure always prints an outcome line ("N passed/failed/
        # error"), so retrying once when none exists is safe; both attempts
        # are recorded in the artifact.
        outcome = re.search(r"\d+ (passed|failed|error|skipped)", combined)
        if proc.returncode != 0 and not outcome:
            continue
        break
    latency_ms = int((time.perf_counter() - started) * 1000)

    passed = proc.returncode == 0
    evidence = [line.strip() for line in combined.splitlines() if re.search(r"\d+ passed", line)]
    summary = evidence[-1] if evidence else f"pytest exit {proc.returncode}"

    artifact = {
        "experiment": "r01_retrieval_router",
        "experiment_id": "r01_retrieval_router",
        "artifact": str(out),
        "skill": SKILL,
        "input": f"pytest {TARGET}",
        "environment": f"msb-v3 repo @ {REPO}",
        "expected_behavior": "all offline retrieval-router tests pass (planner, RRF, dispatch, provenance, endpoint)",
        "actual_behavior": f"exit={proc.returncode} {summary}",
        "latency_ms": latency_ms,
        "errors": [] if passed else [(proc.stdout or "")[-600:] + (proc.stderr or "")[-600:]],
        "attempts": attempts,
        "state_before": {"zero_spend": True, "network": False, "llm": False},
        "state_after": {"exit": proc.returncode, "passed": passed, "summary": summary},
        "recovery": "n/a — read-only verification",
        "false_repair": False,
        "evidence": evidence or [f"pytest exit {proc.returncode}"],
        "verdict": "pass" if passed else "fail",
    }
    out.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    print(json.dumps(artifact, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
