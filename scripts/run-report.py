#!/usr/bin/env python3
"""Generate a run report from the live /metrics/prometheus endpoint.

Closes the M2/M5 metrics-completion gap: the report contains p50/p95
latency (derived from the msb_v3_latency_seconds histogram), query counts,
ActionGate verdicts (allowed/indeterminate/denied), router decisions,
retries, and recoveries — the exact fields the convergence-to-12 plan's
"Metrics completion" exit evidence requires.

Usage:
    python3 scripts/run-report.py [--host http://127.0.0.1:8766] [--out FILE]
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

# Histogram bucket edges of msb_v3_latency_seconds (prometheus_client default
# for seconds: exponential buckets from 0.005 to 10, plus +Inf).
_BUCKETS = [
    0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1.0,
    2.5, 5.0, 7.5, 10.0, float("inf"),
]


def _percentile(bucket_counts: list[tuple[float, int]], q: float) -> float | None:
    """Percentile over cumulative bucket counts.

    Returns the bucket edge at which the cumulative count crosses the
    quantile. If that lands in the +Inf bucket (all observations above the
    largest finite edge), the upper bound is unknowable from buckets alone —
    return the largest finite edge, which is the honest lower bound.
    """
    total = sum(c for _, c in bucket_counts)
    if total == 0:
        return None
    target = total * q
    cum = 0
    for edge, count in bucket_counts:
        cum += count
        if cum >= target:
            return edge if edge != float("inf") else max(
                (e for e, _ in bucket_counts if e != float("inf")), default=None
            )
    return None


def _parse_histogram(metric_lines: list[str]) -> dict[str, list[tuple[float, int]]]:
    """Parse a *_bucket family into per-label cumulative counts."""
    out: dict[str, list[tuple[float, int]]] = defaultdict(list)
    for line in metric_lines:
        # msb_v3_latency_seconds_bucket{harness="agent",le="0.1"} 5
        try:
            meta, _, count_s = line.rstrip().partition(" ")
            le = meta.rsplit('le="', 1)[1].rstrip('"}')
            harness = meta.split('harness="', 1)[1].split('"', 1)[0] if 'harness="' in meta else "?"
            edge = float(le)
        except (IndexError, ValueError):
            continue
        try:
            count = int(float(count_s))  # prometheus_client emits "1.0" for ints
        except ValueError:
            continue
        out[harness].append((edge, count))
    for key in out:
        out[key].sort(key=lambda t: t[0])
    return out


def _counter_family(lines: list[str], prefix: str) -> dict[tuple[str, ...], int]:
    """Parse a counter family (labels -> value), skipping the _total suffix."""
    out: dict[tuple[str, ...], int] = {}
    for line in lines:
        if not line.startswith(prefix):
            continue
        meta, _, count_s = line.rstrip().partition(" ")
        labels = tuple(meta.split("{", 1)[1].rstrip("}").split(",")) if "{" in meta else ()
        try:
            out[labels] = int(float(count_s))  # prometheus_client emits "1.0"
        except ValueError:
            continue
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="http://127.0.0.1:8766")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    url = f"{args.host}/metrics/prometheus"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
            text = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001 — report is best-effort
        print(f"ERROR: cannot fetch {url}: {exc}")
        return 1

    # The endpoint serves the Prometheus text JSON-encoded (content-type
    # application/json with the whole exposition as one quoted string).
    try:
        text = json.loads(text)
    except json.JSONDecodeError:
        pass  # already raw exposition text

    lines = text.splitlines()
    buckets = _parse_histogram([l for l in lines if "_bucket{" in l and "latency" in l])
    verdicts = _counter_family(lines, "msb_v3_actiongate_decisions_total")
    queries = _counter_family(lines, "msb_v3_queries_total")
    router = _counter_family(lines, "msb_v3_router_decisions_total")
    retries = _counter_family(lines, "msb_v3_task_retries_total")
    recoveries = _counter_family(lines, "msb_v3_task_recoveries_total")
    ready = [l for l in lines if l.startswith("msb_v3_ready ")]

    def _strip(labels: tuple[str, ...]) -> str:
        """'verdict=\"allowed\"' -> 'allowed' (keep the label value only)."""
        if not labels:
            return "?"
        first = labels[0]
        if "=" in first:
            return first.split("=", 1)[1].strip('"')
        return first

    report: dict = {
        "generated_at": None,  # filled by caller if desired
        "latency": {
            harness: {
                "p50": _percentile(counts, 0.50),
                "p95": _percentile(counts, 0.95),
                "count": sum(c for _, c in counts),
            }
            for harness, counts in sorted(buckets.items())
        },
        "queries_total": sum(queries.values()),
        "actiongate_verdicts": {
            _strip(labels): v for labels, v in sorted(verdicts.items())
        },
        "router_decisions_total": sum(router.values()),
        "retries_total": sum(retries.values()),
        "recoveries_total": sum(recoveries.values()),
        "ready": ready[0].split()[-1] if ready else None,
    }

    out = args.out or "artifacts/run-report.json"
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
