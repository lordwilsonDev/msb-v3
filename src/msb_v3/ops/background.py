"""Background snapshot: one read-only view of what runs behind the runtime.

Feeds ``GET /ops/background`` (the desktop cockpit's Background section;
docs/superpowers/specs/2026-09-22-background-windows-design.md). Each
subsystem is a reader (fetch raw status in-process) plus a pure classifier
(raw -> Entry). A reader that raises yields an ``unknown`` entry for that
subsystem only; the others still report. Nothing here writes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from msb_v3.cron.parser import CronExpr

OK, WARN, FAIL, UNKNOWN = "ok", "warn", "fail", "unknown"
BUDGET_WARN_RATIO = 0.8
CACHE_TTL_S = 3.0

Raw = Dict[str, Any]


@dataclass
class Entry:
    """One subsystem's state as the cockpit shows it."""

    state: str
    detail: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def parse_ts(value: Any) -> Optional[datetime]:
    """ISO-8601 string -> aware UTC datetime; anything else -> None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def missed_fire(schedule: str, since: datetime, now: datetime, grace: timedelta) -> bool:
    """True when ``schedule`` was due to fire after ``since`` and that fire
    time plus ``grace`` has already passed, i.e. a run was missed."""
    nxt = CronExpr.parse(schedule).next_after(since)
    return nxt is not None and nxt + grace < now


# --- classifiers (pure) ----------------------------------------------------

def classify_cron(raw: Raw, now: datetime) -> Entry:
    """fail: an enabled job's latest run FAILED. warn: an enabled job missed
    a fire, or the scheduler itself is off. Jobs that never ran are not
    judged overdue (there is no baseline)."""
    grace = timedelta(seconds=2 * int(raw.get("tick_s") or 60))
    jobs = raw.get("jobs") or []
    failed: List[str] = []
    overdue: List[str] = []
    for job in jobs:
        if not job.get("enabled"):
            continue
        last = job.get("last_run") or {}
        if last.get("status") == "FAILED":
            failed.append(job["job_id"])
        started = parse_ts(last.get("started_at"))
        if started is None:
            continue
        try:
            if missed_fire(job["schedule"], started, now, grace):
                overdue.append(job["job_id"])
        except ValueError:
            continue  # unparseable schedule: the cron window shows the raw string
    detail = {
        "scheduler_enabled": bool(raw.get("enabled")),
        "job_count": len(jobs),
        "failed": failed,
        "overdue": overdue,
    }
    if failed:
        return Entry(FAIL, detail)
    if overdue or not raw.get("enabled"):
        return Entry(WARN, detail)
    return Entry(OK, detail)
