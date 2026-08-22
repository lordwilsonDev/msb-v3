"""RootCauseEngine — Phase 2: correlate discrepancies into causal graphs.

The DiscrepancyEngine (Phase 1) normalizes *what is wrong*; this engine adds
*why*. It collects live telemetry (wake failures, cron run history, open
discrepancies, boot/restart events), correlates signals into causal edges
with deterministic rules, and ranks root-cause hypotheses by evidence.

Design rules (from the Level 3→4 roadmap):

  - Evidence is authoritative. Correlation is deterministic — every edge
    cites the signals that produced it and a confidence derived from
    evidence strength, never from a model.
  - The LLM seam (``reason()``) is a narrative layer over the evidence,
    never a substitute for it. v1 ships the deterministic surface only;
    a reasoner can consume the same JSON later.
  - Failure-isolated collection: a store that is missing or unreadable
    yields no signals from that source, never a crash.
  - The canonical incident to detect: provider outage (e.g. DeepSeek 402 →
    circuit open) → wake/cron task failures → queue backlog → degraded
    processing (and, with restart signals present, resource exhaustion).

CLI:
    python -m msb_v3.ops.root_cause diagnose --window 24
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from msb_v3.core.config import settings

logger = logging.getLogger(__name__)

SEV_INFO = "info"
SEV_WARN = "warn"
SEV_CRITICAL = "critical"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_ts(ts: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Signals — one normalized telemetry observation
# ---------------------------------------------------------------------------


@dataclass
class Signal:
    ts: str
    source: str  # wake | cron | discrepancy | boot
    kind: str  # provider_failure | task_failure | discrepancy | restart | queue_backlog
    resource: str
    severity: str
    detail: str
    meta: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Failure-string attribution (machine-parseable from observed errors)
# ---------------------------------------------------------------------------

_PROVIDER_PATTERNS: List[tuple] = [
    ("deepseek", re.compile(r"deepseek|ds-api", re.I)),
    ("ollama", re.compile(r"ollama|qwen", re.I)),
    ("qdrant", re.compile(r"qdrant", re.I)),
    ("zapier", re.compile(r"zapier", re.I)),
    ("make", re.compile(r"\bmake\b|integromat", re.I)),
    ("telegram", re.compile(r"telegram", re.I)),
    ("rclone", re.compile(r"rclone|gdrive", re.I)),
]

_ERROR_KINDS: List[tuple] = [
    ("circuit_open", re.compile(r"circuit open", re.I)),
    ("http_402", re.compile(r"HTTP 402|payment required", re.I)),
    ("http_429", re.compile(r"HTTP 429|rate.?limit", re.I)),
    ("timeout", re.compile(r"timed out|timeout", re.I)),
    ("connection", re.compile(r"connectionerror|connecterror|econnrefused|econnreset|refused|unreachable|failed to connect", re.I)),
    ("oom", re.compile(r"killed|exit 137|out of memory|oom", re.I)),
    ("disk", re.compile(r"no space left|errno 28|disk full", re.I)),
]


def parse_error(error: str) -> Dict[str, Any]:
    """Attribute one failure string → ``{provider?, kind?, code?}``.

    Handles the real observed shapes, e.g.:
      "ConnectionError: deepseek circuit open: HTTP 402 (payment required) (cooldown 300.0s)"
    → provider=deepseek, kind=circuit_open, code=402.
    """
    provider = next((name for name, pat in _PROVIDER_PATTERNS if pat.search(error)), None)
    kind = next((k for k, pat in _ERROR_KINDS if pat.search(error)), None)
    m = re.search(r"HTTP (\d{3})", error)
    return {"provider": provider, "kind": kind, "code": int(m.group(1)) if m else None}


# ---------------------------------------------------------------------------
# Correlation edges
# ---------------------------------------------------------------------------


@dataclass
class CausalEdge:
    cause: str  # signal resource (or "<source>:<resource>")
    effect: str
    relation: str  # causes | indicates | follows
    confidence: float
    evidence: List[str]

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RootCause:
    resource: str
    kind: str
    confidence: float
    affected: List[str]
    chain: List[CausalEdge]
    evidence_count: int

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _window_start(now: datetime, hours: float) -> datetime:
    import datetime as dt

    return now - dt.timedelta(hours=hours)


class RootCauseEngine:
    """Collect telemetry → correlate → rank root causes. Deterministic."""

    def __init__(
        self,
        *,
        window_hours: float = 24.0,
        wake_db: Optional[str] = None,
        cron_db: Optional[str] = None,
        discrepancy_store: Optional[Any] = None,
        chain: Optional[Any] = None,
    ) -> None:
        self.window_hours = window_hours
        self.wake_db = wake_db or (
            str(Path(settings.wake_db_path)) if settings.wake_db_path else str(Path(settings.db_path).parent / "runtime" / "wake.db")
        )
        self.cron_db = cron_db or (
            str(Path(settings.cron_db_path)) if settings.cron_db_path else str(Path(settings.db_path).parent / "runtime" / "cron.db")
        )
        self._discrepancies = discrepancy_store
        self._chain = chain

    # -- collection --------------------------------------------------------

    def collect(self) -> List[Signal]:
        now = datetime.now(timezone.utc)
        signals: List[Signal] = []
        signals.extend(self._wake_signals(now))
        signals.extend(self._cron_signals(now))
        signals.extend(self._discrepancy_signals())
        signals.extend(self._boot_signals(now))
        return signals

    def _wake_signals(self, now: datetime) -> List[Signal]:
        """Failed wake messages in the window, aggregated per provider (kinds
        in meta), plus a queue-backlog signal for pending messages."""
        start = _window_start(now, self.window_hours)
        signals: List[Signal] = []
        try:
            with sqlite3.connect(self.wake_db) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT ts, status, error FROM wake_inbox WHERE ts>=?",
                    (start.isoformat(),),
                ).fetchall()
                pending = conn.execute("SELECT COUNT(*) FROM wake_inbox WHERE status='pending'").fetchone()[0]
        except sqlite3.Error:
            return signals
        # Aggregate per provider (kinds stay in meta) so one outage yields
        # one signal, not one per error flavor (402 vs circuit_open vs None).
        buckets: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            if row["status"] != "failed":
                continue
            attr = parse_error(row["error"] or "")
            provider = attr.get("provider")
            key = provider or attr.get("kind") or "unknown"
            b = buckets.setdefault(
                key,
                {"count": 0, "first": row["ts"], "last": row["ts"], "kinds": set(), "error": row["error"] or ""},
            )
            b["count"] += 1
            if attr.get("kind"):
                b["kinds"].add(attr["kind"])
            if row["ts"] < b["first"]:
                b["first"] = row["ts"]
            if row["ts"] > b["last"]:
                b["last"] = row["ts"]
        for resource, b in buckets.items():
            kinds = sorted(b["kinds"])
            signals.append(
                Signal(
                    ts=b["last"],
                    source="wake",
                    kind="provider_failure" if resource in dict(_PROVIDER_PATTERNS) else "task_failure",
                    resource=resource,
                    severity=SEV_WARN,
                    detail=f"{b['count']} failed wake message(s) in window (kinds={kinds or 'unknown'})",
                    meta={"count": b["count"], "first": b["first"], "last": b["last"], "kinds": kinds, "error": b["error"]},
                )
            )
        if pending:
            signals.append(
                Signal(
                    ts=_now(),
                    source="wake",
                    kind="queue_backlog",
                    resource="wake_inbox",
                    severity=SEV_WARN,
                    detail=f"{pending} pending wake message(s)",
                    meta={"pending": pending},
                )
            )
        return signals

    def _cron_signals(self, now: datetime) -> List[Signal]:
        """FAILED cron runs in the window (one signal per job)."""
        start = _window_start(now, self.window_hours)
        signals: List[Signal] = []
        try:
            with sqlite3.connect(self.cron_db) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT run_id, job_id, status, error, started_at FROM cron_runs "
                    "WHERE status='FAILED' AND started_at>=?",
                    (start.isoformat(),),
                ).fetchall()
        except sqlite3.Error:
            return signals
        by_job: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            j = by_job.setdefault(row["job_id"], {"count": 0, "error": row["error"] or "", "ts": row["started_at"]})
            j["count"] += 1
            if row["started_at"] > j["ts"]:
                j["ts"] = row["started_at"]
        for job_id, j in by_job.items():
            signals.append(
                Signal(
                    ts=j["ts"],
                    source="cron",
                    kind="task_failure",
                    resource=f"cron:{job_id}",
                    severity=SEV_WARN,
                    detail=f"{j['count']} failed run(s) of cron job {job_id}",
                    meta={"count": j["count"], "error": j["error"]},
                )
            )
        return signals

    def _discrepancy_signals(self) -> List[Signal]:
        """Open discrepancies from the DiscrepancyEngine store."""
        if self._discrepancies is None:
            from msb_v3.ops.discrepancy import DiscrepancyStore

            self._discrepancies = DiscrepancyStore()
        signals: List[Signal] = []
        try:
            rows = self._discrepancies.query(status="open")
        except Exception:  # noqa: BLE001
            return signals
        for row in rows:
            signals.append(
                Signal(
                    ts=row.get("timestamp", _now()),
                    source="discrepancy",
                    kind="discrepancy",
                    resource=row.get("affected_resource", ""),
                    severity=row.get("severity", SEV_WARN),
                    detail=f"{row.get('subsystem', '')}/{row.get('discrepancy_type', '')}: {row.get('observed_state', '')}",
                    meta={"discrepancy_id": row.get("id", ""), "discrepancy_type": row.get("discrepancy_type", "")},
                )
            )
        return signals

    def _boot_signals(self, now: datetime) -> List[Signal]:
        """Restart events from the audit chain (component=boot)."""
        start = _window_start(now, self.window_hours)
        if self._chain is None:
            try:
                from msb_ledger.chain_anchor import anchored_chain_from_env

                self._chain = anchored_chain_from_env()
            except Exception:  # noqa: BLE001
                return []
        signals: List[Signal] = []
        try:
            records = self._chain.get_chain(component="boot")
        except Exception:  # noqa: BLE001
            return signals
        for r in records:
            ts = getattr(r, "timestamp", None) or _now()
            parsed_ts = _parse_ts(ts)
            if parsed_ts is not None and parsed_ts < start:
                continue
            signals.append(
                Signal(
                    ts=ts,
                    source="boot",
                    kind="restart",
                    resource="msb_v3_server",
                    severity=SEV_WARN,
                    detail="server boot recorded in audit chain",
                    meta={"event_type": getattr(r, "event_type", "")},
                )
            )
        return signals

    # -- correlation -------------------------------------------------------

    def correlate(self, signals: List[Signal]) -> List[CausalEdge]:
        edges: List[CausalEdge] = []
        provider_fails = [s for s in signals if s.kind == "provider_failure"]
        task_fails = [s for s in signals if s.kind == "task_failure"]
        discrepancies = [s for s in signals if s.kind == "discrepancy"]
        backlogs = [s for s in signals if s.kind == "queue_backlog"]
        restarts = [s for s in signals if s.kind == "restart"]

        # R1 — provider failure → task failures on the same resource, or
        # whose error text names the provider (e.g. a cron job failing with
        # "deepseek circuit open").
        for pf in provider_fails:
            related = [
                t
                for t in task_fails
                if pf.resource in t.resource
                or pf.resource in (t.meta.get("error") or "").lower()
            ]
            if related:
                edges.append(
                    CausalEdge(
                        cause=f"{pf.source}:{pf.resource}",
                        effect=",".join(sorted(t.resource for t in related)),
                        relation="causes",
                        confidence=0.9,
                        evidence=[f"{t.detail} (ts={t.ts})" for t in related[:5]],
                    )
                )

        # R2 — provider failure + queue backlog → degraded processing.
        for pf in provider_fails:
            if backlogs:
                edges.append(
                    CausalEdge(
                        cause=f"{pf.source}:{pf.resource}",
                        effect="wake_inbox",
                        relation="causes",
                        confidence=0.8,
                        evidence=[b.detail for b in backlogs[:3]],
                    )
                )

        # R3 — an open discrepancy on the same resource corroborates the
        # provider failure (hard evidence agreeing with observed failures).
        for pf in provider_fails:
            corroborating = [d for d in discrepancies if d.resource == pf.resource]
            if corroborating:
                edges.append(
                    CausalEdge(
                        cause=f"discrepancy:{pf.resource}",
                        effect=f"{pf.source}:{pf.resource}",
                        relation="indicates",
                        confidence=0.85,
                        evidence=[d.detail for d in corroborating[:3]],
                    )
                )

        # R4 — restarts following a provider-failure storm (resource
        # exhaustion chain). Present only when boot signals exist.
        if provider_fails and restarts:
            storm_ts = min(_parse_ts(pf.ts) or datetime.now(timezone.utc) for pf in provider_fails)
            after = [r for r in restarts if (_parse_ts(r.ts) or datetime.min) > storm_ts]
            if after:
                edges.append(
                    CausalEdge(
                        cause=",".join(sorted({f"{p.source}:{p.resource}" for p in provider_fails})),
                        effect="msb_v3_server",
                        relation="follows",
                        confidence=0.6,
                        evidence=[f"restart at {r.ts}" for r in after[:5]],
                    )
                )

        # R5 — cron/task failures with no provider context: job-level issue.
        orphan_crons = [
            t
            for t in task_fails
            if t.resource.startswith("cron:")
            and not any(pf.resource in t.resource or t.resource in pf.resource for pf in provider_fails)
        ]
        for t in orphan_crons:
            edges.append(
                CausalEdge(
                    cause=t.resource,
                    effect=t.resource,
                    relation="indicates",
                    confidence=0.4,
                    evidence=[t.detail],
                )
            )
        return edges

    # -- diagnosis ---------------------------------------------------------

    def _rank_roots(self, signals: List[Signal], edges: List[CausalEdge]) -> List[RootCause]:
        """Root candidates are signal resources that appear as a cause; score
        = summed edge confidence, ranked descending. Provider-failure roots
        are classified provider_outage (observed failures are evidence even
        with no corroborating edge)."""
        signal_kinds = {f"{s.source}:{s.resource}": s.kind for s in signals}
        scores: Dict[str, float] = {}
        affected: Dict[str, List[str]] = {}
        evidence_count: Dict[str, int] = {}
        chains: Dict[str, List[CausalEdge]] = {}
        for e in edges:
            for cause in e.cause.split(","):
                scores[cause] = scores.get(cause, 0.0) + e.confidence
                chains.setdefault(cause, []).append(e)
                evidence_count[cause] = evidence_count.get(cause, 0) + len(e.evidence)
                affected.setdefault(cause, []).extend(a for a in e.effect.split(",") if a and a != cause)
        # Provider failures with no edges are still candidates (observed
        # failures are evidence), just lower-ranked.
        for s in signals:
            if s.kind == "provider_failure":
                key = f"{s.source}:{s.resource}"
                if key not in scores:
                    scores[key] = 0.3
                    chains.setdefault(key, [])
                    affected.setdefault(key, [])
                    evidence_count[key] = int(s.meta.get("count", 1))
        roots = [
            RootCause(
                resource=key.split(":", 1)[-1],
                kind="provider_outage" if signal_kinds.get(key) == "provider_failure" else "system",
                # Summed-edge confidence capped below 1.0: 1.0 is reserved for
                # single hard-fact verification, not accumulated evidence.
                confidence=round(min(score, 0.95), 3),
                affected=sorted(set(affected.get(key, []))),
                chain=chains.get(key, []),
                evidence_count=evidence_count.get(key, 0),
            )
            for key, score in sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        ]
        return roots

    def diagnose(self) -> Dict[str, Any]:
        signals = self.collect()
        edges = self.correlate(signals)
        roots = self._rank_roots(signals, edges)
        summary = self._summarize(roots)
        return {
            "ok": True,
            "ts": _now(),
            "window_hours": self.window_hours,
            "signal_count": len(signals),
            "signals": [s.as_dict() for s in signals],
            "edge_count": len(edges),
            "edges": [e.as_dict() for e in edges],
            "roots": [r.as_dict() for r in roots],
            "summary": summary,
        }

    @staticmethod
    def _summarize(roots: List[RootCause]) -> List[str]:
        lines = []
        for r in roots[:5]:
            if r.affected:
                lines.append(
                    f"{r.resource} ({r.kind}) confidence={r.confidence:.2f} — "
                    f"affects {', '.join(r.affected)}"
                )
            else:
                lines.append(f"{r.resource} ({r.kind}) confidence={r.confidence:.2f}")
        return lines

    def reason(self, diagnosis: Dict[str, Any]) -> str:
        """LLM seam — narrative over evidence (never the authority).

        v1 returns the deterministic summary; a reasoner can be layered over
        ``diagnosis`` (signals + edges + roots) later without touching the
        evidence path.
        """
        return "\n".join(diagnosis.get("summary", []))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli() -> None:
    parser = argparse.ArgumentParser(prog="msb_v3.ops.root_cause", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    d = sub.add_parser("diagnose", help="collect, correlate, rank root causes")
    d.add_argument("--window", type=float, default=24.0, help="lookback window in hours")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)
    if args.command == "diagnose":
        report = RootCauseEngine(window_hours=args.window).diagnose()
        print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    _cli()
