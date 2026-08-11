"""Unit tests for the shared sliding-window rate limiter (msb_v3.core.rate_limit).

Drives the RateLimiter directly with synthetic Starlette Requests, locking in
the edge cases: zero caps, units exceeding the cap, window expiry, per-client
isolation, client-key derivation, and the bounded-entry sweep.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from fastapi import Request

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from msb_v3.core.rate_limit import RateLimiter, client_key  # noqa: E402


def _req(host: str = "1.2.3.4", *, xff: str | None = None, host_header: str | None = None) -> Request:
    headers = []
    if xff is not None:
        headers.append((b"x-forwarded-for", xff.encode()))
    if host_header is not None:
        headers.append((b"host", host_header.encode()))
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": headers,
        "client": (host, 50000),
    }
    return Request(scope)


def _limiter(max_count: int = 10, *, window_s: float = 60.0, max_keys: int = 100) -> RateLimiter:
    return RateLimiter(
        window_s=lambda: window_s,
        max_count=lambda: max_count,
        max_keys=max_keys,
    )


# --- basic sliding window ---------------------------------------------------


def test_allows_up_to_cap_then_refuses() -> None:
    limiter = _limiter(max_count=3)
    req = _req()
    assert [limiter.check(req) for _ in range(3)] == [True, True, True]
    assert limiter.check(req) is False


def test_units_consume_multiples() -> None:
    limiter = _limiter(max_count=5)
    req = _req()
    assert limiter.check(req, units=3) is True
    assert limiter.check(req, units=2) is True  # exactly reaches the cap
    assert limiter.check(req, units=1) is False


def test_units_over_cap_on_fresh_window_refused() -> None:
    """A single request larger than the cap is refused even on a fresh
    window (no partial consumption, no free pass)."""
    limiter = _limiter(max_count=3)
    req = _req()
    assert limiter.check(req, units=4) is False
    # the failed request must not have consumed budget
    assert limiter.check(req, units=3) is True


def test_zero_cap_refuses_everything() -> None:
    limiter = _limiter(max_count=0)
    req = _req()
    assert limiter.check(req) is False
    assert limiter.check(req) is False
    # window expiry does not grant a free pass under a zero cap
    limiter.table["1.2.3.4"] = (time.time() - 61, 0)
    assert limiter.check(req) is False


def test_window_expiry_resets_budget() -> None:
    limiter = _limiter(max_count=2)
    req = _req()
    assert limiter.check(req) is True
    assert limiter.check(req) is True
    assert limiter.check(req) is False
    # age the entry past the window -> fresh budget
    limiter.table["1.2.3.4"] = (time.time() - 61, 2)
    assert limiter.check(req) is True


def test_clients_are_isolated() -> None:
    limiter = _limiter(max_count=1)
    assert limiter.check(_req("1.1.1.1")) is True
    assert limiter.check(_req("2.2.2.2")) is True
    assert limiter.check(_req("1.1.1.1")) is False  # exhausted
    assert limiter.check(_req("2.2.2.2")) is False


def test_clear_resets_all_windows() -> None:
    limiter = _limiter(max_count=1)
    req = _req()
    assert limiter.check(req) is True
    assert limiter.check(req) is False
    limiter.clear()
    assert limiter.check(req) is True


# --- client key derivation --------------------------------------------------


def test_client_key_uses_xff_first() -> None:
    assert client_key(_req("1.2.3.4", xff="203.0.113.9, 10.0.0.1")) == "203.0.113.9"


def test_client_key_peer_host_when_no_xff() -> None:
    assert client_key(_req("203.0.113.9")) == "203.0.113.9"


def test_client_key_host_header_fallback_without_client_scope() -> None:
    # programmatic/httpx requests carry no client scope -> Host header
    scope = {"type": "http", "method": "GET", "path": "/", "headers": [(b"host", b"msb.local")], "client": None}
    assert client_key(Request(scope)) == "msb.local"
    assert client_key(Request({**scope, "headers": []})) == "unknown"


def test_xff_clients_are_isolated_in_limiter() -> None:
    limiter = _limiter(max_count=1)
    a = _req(xff="203.0.113.1")
    b = _req(xff="203.0.113.2")
    assert limiter.check(a) is True
    assert limiter.check(b) is True
    assert limiter.check(a) is False


# --- sweep bounding ---------------------------------------------------------


def test_sweep_prunes_expired_entries_when_table_grows() -> None:
    """Past max_keys, a new client triggers a sweep that drops expired
    entries while keeping fresh ones."""
    limiter = _limiter(max_count=1, window_s=60, max_keys=5)
    req = _req()
    assert limiter.check(req) is True

    # fill the table to max_keys with fresh clients
    for i in range(5):
        assert limiter.check(_req(f"10.0.0.{i}")) is True
    assert len(limiter.table) == 6

    # age the first client's entry so it is expired
    limiter.table["1.2.3.4"] = (time.time() - 61, 1)
    # a brand-new client crosses max_keys and triggers the sweep
    assert limiter.check(_req("10.0.0.99")) is True
    assert "1.2.3.4" not in limiter.table  # expired entry pruned
    assert "10.0.0.99" in limiter.table
    # fresh entries survive the sweep
    assert len(limiter.table) <= 6


def test_sweep_keeps_fresh_entries() -> None:
    limiter = _limiter(max_count=1, window_s=60, max_keys=3)
    for i in range(3):
        assert limiter.check(_req(f"10.0.0.{i}")) is True
    assert len(limiter.table) == 3
    # new client triggers the sweep, but no entry is expired
    assert limiter.check(_req("10.0.0.99")) is True
    assert len(limiter.table) == 4
