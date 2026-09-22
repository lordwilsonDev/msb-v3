"""Background snapshot: one read-only view of what runs behind the runtime.

Feeds ``GET /ops/background`` (the desktop cockpit's Background section;
docs/superpowers/specs/2026-09-22-background-windows-design.md). Each
subsystem is a reader (fetch raw status in-process) plus a pure classifier
(raw -> Entry). A reader that raises yields an ``unknown`` entry for that
subsystem only; the others still report. Nothing here writes.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

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


# --- readers (in-process; never HTTP self-calls) ---------------------------
#
# They reuse the existing status route functions where one exists, so the
# snapshot and the per-subsystem routes can never disagree about the numbers.


async def read_cron() -> Raw:
    from msb_v3.core.config import settings
    from msb_v3.cron.store import CronStore

    store = CronStore()
    jobs = []
    for job in store.list_jobs():
        latest = store.history(job["job_id"], limit=1)
        jobs.append(
            {
                "job_id": job["job_id"],
                "schedule": job["schedule"],
                "enabled": bool(job["enabled"]),
                "last_run": (
                    {"status": latest[0]["status"], "started_at": latest[0]["started_at"]} if latest else None
                ),
            }
        )
    return {"enabled": bool(settings.cron_enabled), "tick_s": int(settings.cron_tick_s), "jobs": jobs}


async def read_governance() -> Raw:
    # The governance singletons (kill switch, ledger, queue) live in the
    # router module; its status() is the one fail-closed reading of them.
    from msb_v3.api import governance as governance_api

    return await governance_api.status()


async def read_automation() -> Raw:
    from msb_v3.api.automation import automation_status

    return automation_status()


async def read_wake() -> Raw:
    from msb_v3.api.wake import wake_status
    from msb_v3.wake.store import WakeStore

    raw = dict(wake_status())
    oldest = WakeStore().pending(limit=1)
    raw["oldest_pending_ts"] = oldest[0]["ts"] if oldest else None
    return raw


async def read_plei(store: Any = None) -> Raw:
    from msb_v3.plei.calibration.store import CalibrationStore

    store = store or CalibrationStore()
    predictions = store.predictions()
    chain_ok, chain_message = store.verify_chain()
    return {
        "predictions": len(predictions),
        "outcomes": store.outcome_count(),
        "pairs": store.pair_count(),
        "chain_ok": chain_ok,
        "chain_message": chain_message,
        "last_forecast_at": predictions[-1].forecast_at if predictions else None,
    }


Reader = Callable[[], Awaitable[Raw]]
Classifier = Callable[[Raw, datetime], Entry]

READERS: Dict[str, Tuple[Reader, Classifier]] = {
    "cron": (read_cron, classify_cron),
    "governance": (read_governance, classify_governance),
    "automation": (read_automation, classify_automation),
    "wake": (read_wake, classify_wake),
    "plei": (read_plei, classify_plei),
}


async def build_snapshot(
    readers: Optional[Dict[str, Tuple[Reader, Classifier]]] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Read and classify every subsystem. One broken subsystem becomes
    ``unknown`` with its error; it never blanks the others."""
    readers = READERS if readers is None else readers
    now = now or datetime.now(timezone.utc)
    subsystems: Dict[str, Any] = {}
    for name, (read, classify) in readers.items():
        try:
            entry = classify(await read(), now)
        except Exception as exc:  # noqa: BLE001 - isolation is the point
            entry = Entry(UNKNOWN, {}, f"{type(exc).__name__}: {exc}")
        subsystems[name] = entry.as_dict()
    return {"generated_at": now.isoformat(), "subsystems": subsystems}


class SnapshotCache:
    """Serve one snapshot for ``ttl_s`` so several polling windows share it.
    Two concurrent misses may both build; that is harmless (reads only)."""

    def __init__(self, ttl_s: float = CACHE_TTL_S, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl_s
        self._clock = clock
        self._value: Optional[Dict[str, Any]] = None
        self._at = 0.0

    async def get(self, build: Callable[[], Awaitable[Dict[str, Any]]]) -> Dict[str, Any]:
        now = self._clock()
        if self._value is None or now - self._at >= self._ttl:
            self._value = await build()
            self._at = now
        return self._value
