"""Failure Model — failure modes from audit data and component structure.

Derives failure modes from:
    1. Audit.jsonl — recent failures in the evidence stream
    2. Dependency graph — single points of failure (high fan-in modules)
    3. Operational state — launchd agent failures, disk warnings

Each failure mode carries severity, likelihood, and a recovery assessment.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from msb_v3.plei.twin import ProjectTwin


@dataclass(slots=True)
class FailureMode:
    """One known or predicted failure mode."""

    kind: str  # e.g. "provider_outage", "disk_saturation", "dependency_failure"
    severity: int  # 1–10
    likelihood: float  # 0.0–1.0
    component: str  # which component is affected
    evidence: str  # short description of evidence
    recovery_assessment: str  # "automatic", "manual", "unknown"


@dataclass(slots=True)
class FailureReport:
    """All known failure modes, ranked by risk."""

    total_modes: int
    modes: list[FailureMode] = field(default_factory=list)
    risk_distribution: dict[str, float] = field(default_factory=dict)  # kind → risk score


def analyze_failures(twin: ProjectTwin) -> FailureReport:
    """Analyze failure modes from audit data and component structure."""
    modes: list[FailureMode] = []

    # --- 1. Audit-jsonl analysis ---
    try:
        audit_path = Path.cwd() / "logs" / "audit.jsonl"
        if audit_path.is_file():
            lines = audit_path.read_text().strip().split("\n")
            # Scan last 500 entries for failures
            recent = lines[-500:]
            failure_counts: dict[str, int] = {}
            for line in recent:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                status = entry.get("status", "")
                result = entry.get("result", {})
                ok = result.get("ok", True) if isinstance(result, dict) else True
                if status == "FAILURE" or (not ok and status != "SUCCESS"):
                    event = entry.get("event", "unknown")
                    failure_counts[event] = failure_counts.get(event, 0) + 1

            for event, count in failure_counts.items():
                likelihood = min(0.95, count / 50.0)  # normalize over 50 samples
                if "circuit" in event.lower() or "deepseek" in event.lower():
                    modes.append(FailureMode(
                        kind="provider_outage",
                        severity=6,
                        likelihood=round(likelihood, 2),
                        component="DeepSeek API",
                        evidence=f"{count} circuit-open events in last 500 audit entries",
                        recovery_assessment="automatic (circuit breaker cooldown)",
                    ))
                elif "cron" in event.lower():
                    modes.append(FailureMode(
                        kind="scheduler_failure",
                        severity=5,
                        likelihood=round(likelihood, 2),
                        component="cron scheduler",
                        evidence=f"{count} cron job failures in recent audit",
                        recovery_assessment="automatic (retry + overlap guard)",
                    ))
    except Exception:
        pass

    # --- 2. Dependency graph — single points of failure ---
    try:
        from msb_v3.plei.dependency.graph import build_dependency_graph
        dep_graph = build_dependency_graph(Path.cwd())
        for bn in dep_graph.bottlenecks[:3]:
            if bn["fan_in"] >= 3:
                instability = bn.get("instability", 0.5)
                modes.append(FailureMode(
                    kind="dependency_failure",
                    severity=min(9, 3 + bn["fan_in"]),
                    likelihood=round(0.20 * bn["fan_in"] * instability, 2),
                    component=bn["module"],
                    evidence=(
                        f"Fan-in: {bn['fan_in']} modules depend on {bn['module']}; "
                        f"instability: {instability}"
                    ),
                    recovery_assessment="manual",
                ))
    except Exception:
        pass

    # --- 3. Operational state ---
    try:
        live_health = twin.evidence.live_health.value
        if isinstance(live_health, dict):
            circuit = live_health.get("deepseek_circuit", {})
            if isinstance(circuit, dict) and circuit.get("open"):
                modes.append(FailureMode(
                    kind="provider_outage",
                    severity=6,
                    likelihood=0.90,
                    component="DeepSeek API",
                    evidence=(
                        f"Circuit open: {circuit.get('reason', 'unknown')} "
                        f"(cooldown: {circuit.get('cooldown_remaining_s', 0)}s remaining)"
                    ),
                    recovery_assessment="automatic (cooldown)",
                ))
    except Exception:
        pass

    # --- 4. Static known failure modes ---
    static_modes = [
        FailureMode(
            kind="disk_saturation",
            severity=7,
            likelihood=0.50,
            component="Mac Mini SSD",
            evidence="89% disk usage at last audit; LS Studio + Docker Desktop VM active",
            recovery_assessment="manual (prune/reclaim or external storage)",
        ),
        FailureMode(
            kind="single_point_of_failure",
            severity=8,
            likelihood=0.35,
            component="msb-v3 (single Mac Mini)",
            evidence="All 13 launchd agents on one machine; replication target configured but not validated",
            recovery_assessment="manual (provision replica node)",
        ),
        FailureMode(
            kind="license_expiry",
            severity=4,
            likelihood=0.05,
            component="source-license",
            evidence="License valid, scope=full, no expiry date",
            recovery_assessment="manual (re-issue from owner key)",
        ),
    ]
    modes.extend(static_modes)

    # Rank by risk (severity × likelihood)
    modes.sort(key=lambda m: -(m.severity * m.likelihood))

    # Distribution
    risk_dist: dict[str, float] = {}
    for m in modes:
        risk_dist[m.kind] = risk_dist.get(m.kind, 0.0) + m.severity * m.likelihood

    return FailureReport(
        total_modes=len(modes),
        modes=modes,
        risk_distribution={k: round(v, 1) for k, v in sorted(risk_dist.items())},
    )


def failure_report_as_dict(report: FailureReport) -> dict[str, Any]:
    return {
        "total_modes": report.total_modes,
        "risk_distribution": report.risk_distribution,
        "modes": [
            {
                "kind": m.kind,
                "severity": m.severity,
                "likelihood": m.likelihood,
                "risk_score": round(m.severity * m.likelihood, 1),
                "component": m.component,
                "evidence": m.evidence,
                "recovery": m.recovery_assessment,
            }
            for m in report.modes
        ],
    }