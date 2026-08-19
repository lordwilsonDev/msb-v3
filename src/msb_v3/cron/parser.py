"""5-field cron expression parser + matcher (no external dependency).

The project pins every dependency to a hashed lockfile and runs a pip-audit
gate, so a cron engine was written in-house instead of pulling croniter: the
language we need is a well-bounded subset, and every rule below is unit-tested
(tests/cron/test_parser.py).

Supported syntax (per field):

    *          every value
    */N        every N-th value, starting at the field minimum
    1,3,5      explicit list
    1-5        inclusive range
    1-5/2      range with step

Fields: minute (0-59), hour (0-23), day-of-month (1-31), month (1-12),
day-of-week (0-7, where 0 and 7 both mean Sunday). Names (JAN/MON) are
deliberately NOT supported — numeric-only keeps the parser tiny; the CLI
documents this.

Semantics follow Vixie cron: if BOTH day-of-month and day-of-week are
restricted (non-``*``), the expression matches when EITHER matches (the
classic ``0 0 1 * 1`` = "first of the month OR any Monday" behavior).
``CronExpr.matches()`` is pure — no timezone handling; callers pass naive
UTC datetimes and the scheduler runs on UTC.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

_FIELD_RANGES: Tuple[Tuple[int, int], ...] = (
    (0, 59),   # minute
    (0, 23),   # hour
    (1, 31),   # day of month
    (1, 12),   # month
    (0, 7),    # day of week (0 and 7 = Sunday)
)
_NORMALIZE_DOW = {7: 0}  # Vixie cron: 7 == Sunday == 0

_TOKEN_RE = re.compile(r"^(\d+|\*)(?:-(\d+|\*))?(?:/(\d+))?$")


def _parse_field(expr: str, lo: int, hi: int) -> List[int]:
    """Parse one field into the sorted list of values it matches."""
    values: List[int] = []
    for part in expr.split(","):
        part = part.strip()
        if not part:
            raise ValueError(f"empty field element in {expr!r}")
        m = _TOKEN_RE.match(part)
        if not m:
            raise ValueError(f"invalid cron field element {part!r} (in {expr!r})")
        start_s, end_s, step_s = m.groups()
        start = lo if start_s == "*" else int(start_s)
        if end_s is None:
            # A bare number means exactly that value ("5" != "5-59"); a bare
            # star means the whole field ("*" == "lo-hi").
            end = start if start_s != "*" else hi
        elif end_s == "*":
            end = hi
        else:
            end = int(end_s)
        step = int(step_s) if step_s else 1
        if start < lo or end > hi or start > end:
            raise ValueError(f"cron range {part!r} out of bounds for {lo}-{hi}")
        if step < 1:
            raise ValueError(f"cron step must be >= 1 in {part!r}")
        values.extend(range(start, end + 1, step))
    if not values:
        raise ValueError(f"cron field {expr!r} matches nothing")
    return sorted(set(values))


def _canonical_dow(values: List[int]) -> List[int]:
    """Fold day-of-week 7 -> 0 so ``7`` and ``0`` are the same Sunday."""
    return sorted(set(_NORMALIZE_DOW.get(v, v) for v in values))


@dataclass(frozen=True)
class CronExpr:
    """A parsed 5-field cron expression, e.g. ``CronExpr.parse("*/15 * * * *")``."""

    minutes: List[int]
    hours: List[int]
    days_of_month: List[int]
    months: List[int]
    days_of_week: List[int]
    raw: str

    @classmethod
    def parse(cls, expr: str) -> "CronExpr":
        """Parse ``"min hour dom month dow"``. Raises ValueError on any
        malformed field — the store refuses to persist an unparseable
        expression, so a bad job can never be created."""
        parts = expr.strip().split()
        if len(parts) != 5:
            raise ValueError(f"cron expression must have 5 fields, got {len(parts)}: {expr!r}")
        minutes = _parse_field(parts[0], *_FIELD_RANGES[0])
        hours = _parse_field(parts[1], *_FIELD_RANGES[1])
        dom = _parse_field(parts[2], *_FIELD_RANGES[2])
        months = _parse_field(parts[3], *_FIELD_RANGES[3])
        dow = _canonical_dow(_parse_field(parts[4], *_FIELD_RANGES[4]))
        return cls(
            minutes=minutes,
            hours=hours,
            days_of_month=dom,
            months=months,
            days_of_week=dow,
            raw=expr.strip(),
        )

    # Vixie cron's OR rule: a restricted dom with a restricted dow matches
    # when either field matches (so ``0 0 1 * 1`` fires on the 1st AND on
    # Mondays). If either field is ``*`` (unrestricted), the other must match
    # and the unrestricted one is ignored.
    @property
    def _dom_restricted(self) -> bool:
        return len(self.days_of_month) < 31

    @property
    def _dow_restricted(self) -> bool:
        return len(self.days_of_week) < 7

    def matches(self, dt: datetime) -> bool:
        """True when the expression fires at this exact minute."""
        if dt.minute not in self.minutes:
            return False
        if dt.hour not in self.hours:
            return False
        if dt.month not in self.months:
            return False
        # Cron day-of-week: 0=Sunday..6=Saturday. Python's weekday() is
        # 0=Monday..6=Sunday — shift by one to compare on the cron axis.
        cron_dow = (dt.weekday() + 1) % 7
        day_matches = dt.day in self.days_of_month
        dow_matches = cron_dow in self.days_of_week
        if self._dom_restricted and self._dow_restricted:
            return day_matches or dow_matches
        if self._dom_restricted:
            return day_matches
        if self._dow_restricted:
            return dow_matches
        return True

    def next_after(self, dt: datetime) -> Optional[datetime]:
        """The next minute (strictly after ``dt``) this expression fires, or
        None if none exists within 5 years (defensive bound — a valid cron
        expression always fires again, but the scan must terminate)."""
        probe = dt.replace(second=0, microsecond=0) + timedelta(minutes=1)
        horizon = dt + timedelta(days=365 * 5)
        while probe <= horizon:
            if self.matches(probe):
                return probe
            probe += timedelta(minutes=1)
        return None

    def next_run(self, after: Optional[datetime] = None) -> Optional[datetime]:
        """Convenience wrapper: next firing time after ``after`` (defaults to
        now UTC). Used for the ``next_run`` column on job listings."""
        from datetime import timezone

        base = after or datetime.now(timezone.utc)
        return self.next_after(base)
