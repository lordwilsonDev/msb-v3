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


def _over_ratio(spent: Any, limit: Any) -> bool:
    """spent/limit > BUDGET_WARN_RATIO; limits <= 0 mean unlimited/unset."""
    if not isinstance(limit, (int, float)) or not isinstance(spent, (int, float)) or limit <= 0:
        return False
    return spent / limit > BUDGET_WARN_RATIO


def classify_governance(raw: Raw, now: datetime) -> Entry:
    """fail: kill switch armed (including fail-closed on unreadable state).
    warn: a scoped lockdown is armed, the scope list is unreadable, or a
    budget is over 80%."""
    if "killswitch" not in raw:
        return Entry(UNKNOWN, {}, "no kill-switch state in governance status")
    ks = raw.get("killswitch") or {}
    scope_rows = ks.get("scopes") or []
    scopes = [s for s in scope_rows if "error" not in s]
    scope_error = len(scopes) != len(scope_rows)
    hot = [cat for cat, b in (raw.get("budgets") or {}).items() if _over_ratio(b.get("spent"), b.get("limit"))]
    detail = {
        "killswitch_armed": bool(ks.get("armed")),
        "fail_closed": bool(ks.get("fail_closed")),
        "armed_scopes": len(scopes),
        "scope_read_error": scope_error,
        "budgets_over_80pct": hot,
        "pending_approvals": int((raw.get("approvals") or {}).get("pending", 0)),
    }
    if ks.get("armed"):
        return Entry(FAIL, detail)
    if scopes or scope_error or hot:
        return Entry(WARN, detail)
    return Entry(OK, detail)


def classify_automation(raw: Raw, now: datetime) -> Entry:
    """warn: spend is over 80% of the USD cap. Dry-run is reported, not judged."""
    budget = raw.get("budget") or {}
    providers = raw.get("providers") or {}
    detail = {
        "dry_run": bool(raw.get("dry_run")),
        "cap_usd": budget.get("cap_usd"),
        "spent_usd": budget.get("spent_usd"),
        "remaining_usd": budget.get("remaining_usd"),
        "providers_configured": sum(1 for p in providers.values() if p.get("configured")),
        "providers_total": len(providers),
    }
    if _over_ratio(budget.get("spent_usd"), budget.get("cap_usd")):
        return Entry(WARN, detail)
    return Entry(OK, detail)


def classify_wake(raw: Raw, now: datetime) -> Entry:
    """fail: wake is enabled but its cron job is missing (nothing will answer).
    warn: the oldest pending message has sat through a missed wake cycle.
    Disabled wake is a configuration choice, reported as ok."""
    detail = {
        "enabled": bool(raw.get("enabled")),
        "schedule": raw.get("schedule"),
        "pending": int(raw.get("pending") or 0),
        "outbox_count": raw.get("outbox_count"),
        "job_present": bool(raw.get("job_present")),
        "oldest_pending_ts": raw.get("oldest_pending_ts"),
    }
    if not detail["enabled"]:
        return Entry(OK, detail)
    if not detail["job_present"]:
        return Entry(FAIL, detail)
    oldest = parse_ts(raw.get("oldest_pending_ts"))
    if detail["pending"] and oldest is not None and isinstance(raw.get("schedule"), str):
        try:
            if missed_fire(raw["schedule"], oldest, now, timedelta(minutes=2)):
                return Entry(WARN, detail)
        except ValueError:
            pass
    return Entry(OK, detail)


def classify_plei(raw: Raw, now: datetime) -> Entry:
    """fail: the calibration hash chain does not verify."""
    detail = dict(raw)
    return Entry(OK if raw.get("chain_ok") else FAIL, detail)
