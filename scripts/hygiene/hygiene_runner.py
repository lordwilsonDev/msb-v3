#!/usr/bin/env python3

"""Engineering hygiene runner — single CLI index over the standalone runners.

This file is the ONE entry point for the MSB-v3 hygiene suite. It no longer
contains its own experiment implementations (those diverged from the
standalone runners); instead it delegates every experiment to its standalone
runner file in this directory and aggregates the verdicts into a factory-style
weakest-verdict gate.

Usage:
    python hygiene_runner.py --all                 # run every experiment
    python hygiene_runner.py h05                   # run one experiment
    python hygiene_runner.py --only h05 h07        # run a subset
    python hygiene_runner.py --list                # list experiments
    python hygiene_runner.py --json                # machine-readable aggregate

Exit code: 0 if no experiment FAILED (pass/partial/blocked are non-fatal),
1 if any experiment reports `fail`.

Delegation contract: each standalone runner prints a final JSON object on
stdout with at least {"experiment", "verdict", "artifact"}. That JSON line is
parsed here; nothing else from child stdout is trusted.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = Path(os.environ.get("MSB_REPO", "/Users/lordwilson/msb-v3"))
HERE = Path(__file__).resolve().parent
EVIDENCE_DIR = REPO / "artifacts" / "hygiene"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

PY = os.environ.get("MSB_PYTHON", sys.executable)

# Canonical experiment registry: name -> standalone runner file.
# Every experiment now lives in ONE place (its standalone runner), so there is
# a single source of truth per experiment.
EXPERIMENTS: dict[str, Path] = {
    "h01_load": HERE / "h01_load_runner.py",
    "h02_restart": HERE / "h02_restart_runner.py",
    "h03_idempotency": HERE / "h03_idempotency_runner.py",
    "h04_race": HERE / "h04_race_runner.py",
    "h05_contract": HERE / "h05_contract_fuzzing_runner.py",
    "h06_audit": HERE / "h06_audit_tampering_runner.py",
    "h07_heal": HERE / "h07_auto_healing_runner.py",
    "h08_chaos": HERE / "h08_chaos_runner.py",
    "r01_retrieval_router": HERE / "r01_retrieval_router_runner.py",
    "h09_deps": HERE / "h09_dependency_subtraction_runner.py",
    "h10_resource": HERE / "h10_resource_chaos_runner.py",
}

# Weakest-verdict ordering: fail < partial < blocked < pass < unknown.
_WEIGHT = {"fail": 0, "partial": 1, "blocked": 2, "pass": 3, "unknown": 4}


def resolve_name(name: str) -> str:
    """Accept short names (h05) and full names (h05_contract) alike."""
    if name in EXPERIMENTS:
        return name
    for full in EXPERIMENTS:
        if full.startswith(name + "_") or full == name:
            return full
    return name


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def run_experiment(name: str, runner: Path) -> dict[str, Any]:
    """Run one standalone runner, parse its final JSON summary line."""
    try:
        proc = subprocess.run(
            [PY, str(runner)],
            capture_output=True, text=True, timeout=300, check=False,
        )
    except subprocess.TimeoutExpired:
        return {"experiment": name, "verdict": "fail", "artifact": None,
                "error": "runner timed out after 300s"}
    summary: dict[str, Any] = {"experiment": name, "verdict": "unknown",
                               "artifact": None, "returncode": proc.returncode}
    parsed = _extract_json_object(proc.stdout)
    if isinstance(parsed, dict) and "verdict" in parsed:
        summary.update({k: parsed.get(k) for k in ("experiment", "verdict", "artifact")})
    if summary.get("verdict") == "unknown" and proc.returncode != 0:
        summary["verdict"] = "fail"
        summary["error"] = (proc.stderr or proc.stdout or "").strip()[-300:]
    return summary


def _extract_json_object(text: str, key: str | None = "verdict") -> dict[str, Any] | None:
    """Parse the TOP-LEVEL JSON object in text that contains `key`.

    Standalone runners print their summary with `json.dumps(indent=2)` so the
    object spans multiple lines. A naive line-by-line scan can never parse it,
    and a naive rfind-based brace walk grabs the innermost nested object. This
    scans all complete top-level objects and returns the one containing the
    key (or the last one if key is None). Matches run_factory.py's version so
    the two trees share one robust implementation.
    """
    raw = text.strip()
    if not raw:
        return None
    candidates: list[dict[str, Any]] = []
    i = 0
    n = len(raw)
    while i < n:
        if raw[i] == "{":
            depth = 0
            for j in range(i, n):
                if raw[j] == "{":
                    depth += 1
                elif raw[j] == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            obj = json.loads(raw[i:j + 1])
                        except json.JSONDecodeError:
                            obj = None
                        if isinstance(obj, dict):
                            candidates.append(obj)
                        i = j + 1
                        break
            else:
                break
        else:
            i += 1
    if key is not None:
        for obj in candidates:
            if key in obj:
                return obj
    return candidates[-1] if candidates else None


def weakest_verdict(results: list[dict[str, Any]]) -> str:
    verdicts = [r.get("verdict", "unknown") for r in results]
    worst = min(verdicts, key=lambda v: _WEIGHT.get(v, 4))
    return worst


def build_aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    gate = weakest_verdict(results)
    return {
        "timestamp": now(),
        "environment": {
            "repo": str(REPO),
            "python": PY,
            "experiments": len(results),
        },
        "results": results,
        "factory_verdict": gate,
        "factory_gate": {
            "any_fail": any(r.get("verdict") == "fail" for r in results),
            "any_unknown": any(r.get("verdict") == "unknown" for r in results),
            "all_pass": all(r.get("verdict") == "pass" for r in results),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="MSB-v3 hygiene runner (delegating index)")
    parser.add_argument("experiments", nargs="*", help="experiment names (e.g. h05 h07)")
    parser.add_argument("--all", action="store_true", help="run every experiment")
    parser.add_argument("--only", nargs="+", default=None, help="run only the named experiments")
    parser.add_argument("--list", action="store_true", help="list experiments and exit")
    parser.add_argument("--json", action="store_true", help="print aggregate as JSON only")
    args = parser.parse_args()

    if args.list:
        for name, runner in EXPERIMENTS.items():
            print(f"{name:16s} {runner.name}")
        return 0

    if args.only:
        names = args.only
    elif args.all:
        names = list(EXPERIMENTS)
    elif args.experiments:
        names = args.experiments
    else:
        parser.error("provide --all, --only, or experiment names")

    names = [resolve_name(n) for n in names]
    unknown = [n for n in names if n not in EXPERIMENTS]
    if unknown:
        parser.error(f"unknown experiment(s): {', '.join(unknown)}")

    results: list[dict[str, Any]] = []
    for name in names:
        print(f"=== {name} ===", flush=True)
        summary = run_experiment(name, EXPERIMENTS[name])
        results.append(summary)
        print(json.dumps(summary, indent=2), flush=True)

    aggregate = build_aggregate(results)
    out = EVIDENCE_DIR / "hygiene_aggregate.json"
    out.write_text(json.dumps(aggregate, indent=2, default=str), encoding="utf-8")
    if args.json:
        print(json.dumps(aggregate, indent=2, default=str))
    else:
        print(f"\n=== aggregate === verdict={aggregate['factory_verdict']} "
              f"experiments={len(results)}")
        for r in results:
            print(f"  {r.get('experiment'):16s} {r.get('verdict'):8s} "
                  f"{r.get('artifact') or ''}")
        print(f"Aggregate: {out}")
    return 1 if aggregate["factory_verdict"] == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
