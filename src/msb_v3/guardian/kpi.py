"""Append one KPI row per run and regenerate the rolling rollup (doc 4 §8).

``kpi.jsonl`` is the machine record (one line per run); ``kpi.md`` is a
human-readable trailing view regenerated each run.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .config import GuardianConfig
from .reasoning import GuardianResult


def _kpi_dir(cfg: GuardianConfig, dry_run: bool) -> Path:
    if dry_run:
        return cfg.ledger.local_state_dir / "dry-run" / "kpi"
    return cfg.ledger.vault_dir / "kpi"


def _row(result: GuardianResult, runtime_s: float | None) -> dict[str, object]:
    sev = Counter(f.severity for f in result.findings)
    return {
        "run_id": result.run_id,
        "ts": datetime.now(tz=timezone.utc).isoformat(),
        "decision": result.decision,
        "findings_total": len(result.findings),
        "findings_by_sev": dict(sev),
        "escalations": len(result.escalations),
        "escalation_reasons": [e.reason for e in result.escalations],
        "proposals": len(result.proposals),
        "controls_passed": result.controls_passed,
        "reasoning_tokens": result.reasoning_tokens,
        "runtime_seconds": runtime_s,
    }


def _render_md(rows: list[dict[str, object]]) -> str:
    n = len(rows)
    dec = Counter(str(r.get("decision")) for r in rows)
    esc = sum(v if isinstance(v := r.get("escalations", 0), int) else 0 for r in rows)
    lines = [
        f"# S-AOS Guardian KPIs — {n} run(s) recorded (updated {rows[-1]['run_id'] if rows else '—'})",
        "",
        f"Decision mix: NO_ACTION {dec['NO_ACTION']} · PROPOSE {dec['PROPOSE']} · ESCALATE {dec['ESCALATE']}",
        f"Total escalations: {esc}",
        "",
        "## Per-run log",
        "",
        "| run_id | decision | findings | escalations | proposals | tokens |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows[-30:]:
        lines.append(
            f"| {r['run_id']} | {r['decision']} | {r['findings_total']} "
            f"| {r['escalations']} | {r['proposals']} | {r.get('reasoning_tokens') or '—'} |"
        )
    lines.append("")
    return "\n".join(lines)


def record(
    cfg: GuardianConfig,
    result: GuardianResult,
    *,
    runtime_s: float | None = None,
    dry_run: bool = False,
) -> dict[str, str]:
    kdir = _kpi_dir(cfg, dry_run)
    kdir.mkdir(parents=True, exist_ok=True)
    jsonl = kdir / "kpi.jsonl"
    md = kdir / "kpi.md"

    row = _row(result, runtime_s)
    with jsonl.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")

    rows: list[dict[str, object]] = []
    for line in jsonl.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    md.write_text(_render_md(rows), encoding="utf-8")

    return {"kpi_jsonl": str(jsonl), "kpi_md": str(md)}
