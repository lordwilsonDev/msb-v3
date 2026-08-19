"""Tests for the 5-field cron expression parser (cron/parser.py)."""

from __future__ import annotations

from datetime import datetime

import pytest

from msb_v3.cron.parser import CronExpr


def _dt(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M")


# --- parsing --------------------------------------------------------------

def test_parse_star_fields() -> None:
    expr = CronExpr.parse("* * * * *")
    assert expr.minutes == list(range(0, 60))
    assert expr.hours == list(range(0, 24))
    assert expr.days_of_month == list(range(1, 32))
    assert expr.months == list(range(1, 13))
    assert expr.days_of_week == list(range(0, 7))


def test_parse_step() -> None:
    expr = CronExpr.parse("*/15 * * * *")
    assert expr.minutes == [0, 15, 30, 45]
    assert expr.matches(_dt("2026-08-18 10:15"))
    assert not expr.matches(_dt("2026-08-18 10:10"))


def test_parse_range_and_list() -> None:
    expr = CronExpr.parse("0 9-17/2 1,15 * 1-5")
    assert expr.hours == [9, 11, 13, 15, 17]
    assert expr.days_of_month == [1, 15]
    # DOW 1-5 is Mon-Fri on the cron axis (0=Sunday), and the expression is
    # unrestricted on dom, so it should NOT fire on a Sunday.
    assert expr.days_of_week == [1, 2, 3, 4, 5]
    assert expr.matches(_dt("2026-08-18 13:00"))  # Tue 13:00 -> hour 13 in range
    assert not expr.matches(_dt("2026-08-23 13:00"))  # Sunday


def test_parse_dow_7_is_sunday() -> None:
    # 2026-08-18 is a Tuesday (weekday 1).
    assert CronExpr.parse("0 0 * * 7").matches(_dt("2026-08-23 00:00"))  # Sunday
    assert CronExpr.parse("0 0 * * 0").matches(_dt("2026-08-23 00:00"))
    assert not CronExpr.parse("0 0 * * 7").matches(_dt("2026-08-18 00:00"))  # Tuesday


def test_parse_validation_errors() -> None:
    for bad in (
        "not-a-cron",
        "* * * *",           # 4 fields
        "* * * * * *",       # 6 fields
        "60 * * * *",        # minute out of range
        "* 24 * * *",        # hour out of range
        "* * 32 * *",        # dom out of range
        "* * * 13 *",        # month out of range
        "* * * * 8",         # dow out of range
        "1-5-3 * * * *",     # malformed token
        "*/0 * * * *",       # step 0
        "5-2 * * * *",       # start > end
        "a * * * *",         # non-numeric
        "* * * * * * *",     # too many
    ):
        with pytest.raises(ValueError):
            CronExpr.parse(bad)


def test_parse_accepts_whitespace() -> None:
    assert CronExpr.parse("  0  2  *  *  *  ").matches(_dt("2026-08-18 02:00"))


# --- matching -------------------------------------------------------------

def test_matches_minute_hour() -> None:
    expr = CronExpr.parse("30 14 * * *")
    assert expr.matches(_dt("2026-08-18 14:30"))
    assert not expr.matches(_dt("2026-08-18 14:31"))
    assert not expr.matches(_dt("2026-08-18 13:30"))


def test_matches_month_restriction() -> None:
    expr = CronExpr.parse("0 0 1 6 *")  # June 1
    assert expr.matches(_dt("2026-06-01 00:00"))
    assert not expr.matches(_dt("2026-07-01 00:00"))


def test_dom_dow_or_semantics() -> None:
    # Vixie: "0 0 1 * 1" fires on the 1st OR on Mondays.
    expr = CronExpr.parse("0 0 1 * 1")
    assert expr.matches(_dt("2026-06-01 00:00"))  # the 1st (a Monday, but still)
    assert expr.matches(_dt("2026-06-08 00:00"))  # a Monday (8th)
    assert not expr.matches(_dt("2026-06-03 00:00"))  # Wed the 3rd


def test_dom_restricted_dow_star() -> None:
    expr = CronExpr.parse("0 0 15 * *")  # 15th of any month
    assert expr.matches(_dt("2026-08-15 00:00"))
    assert not expr.matches(_dt("2026-08-16 00:00"))


# --- next_after -----------------------------------------------------------

def test_next_after_basic() -> None:
    expr = CronExpr.parse("*/30 * * * *")
    assert expr.next_after(_dt("2026-08-18 10:35")) == _dt("2026-08-18 11:00")
    assert expr.next_after(_dt("2026-08-18 10:29")) == _dt("2026-08-18 10:30")


def test_next_after_rolls_day() -> None:
    expr = CronExpr.parse("0 2 * * *")
    assert expr.next_after(_dt("2026-08-18 03:00")) == _dt("2026-08-19 02:00")


def test_next_after_month_boundary() -> None:
    expr = CronExpr.parse("0 0 1 * *")
    assert expr.next_after(_dt("2026-08-31 12:00")) == _dt("2026-09-01 00:00")


def test_next_after_dow() -> None:
    expr = CronExpr.parse("0 9 * * 1")  # Mondays 09:00
    assert expr.next_after(_dt("2026-08-18 10:00")) == _dt("2026-08-24 09:00")
