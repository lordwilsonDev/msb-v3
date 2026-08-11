"""Shared in-process sliding-window rate limiter.

One implementation for every endpoint that needs a per-client cap. The
/research/assistant/run middleware and the /v1/embeddings adapter both use
it; the window/max values come from a callable so middleware constants and
live config both work.

In-process only — exact counts assume a single worker (same caveat as the
original app.py middleware). ``X-Forwarded-For`` is honored for client
identity and is only meaningful behind a trusted proxy; a caller could
otherwise spoof it to reset its own window.
"""

from __future__ import annotations

import time
from collections import defaultdict
from ipaddress import ip_address
from threading import Lock
from typing import Callable, Dict, Tuple

from fastapi import Request

WindowEntry = Tuple[float, int]  # (window_start, count)


def client_key(request: Request) -> str:
    """Best-effort client identity. Honors X-Forwarded-For (first hop only)
    and falls back to the socket peer."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    # Not every request carries a client scope (e.g. programmatic/httpx
    # requests passed into tests) — fall back to the header or "unknown".
    client = getattr(request, "client", None)
    host = getattr(client, "host", None) if client else None
    if host is None:
        return request.headers.get("host", "unknown")
    try:
        return str(ip_address(host))
    except ValueError:
        return host


class RateLimiter:
    """Sliding-window per-client cap.

    ``window_s`` and ``max_count`` are callables evaluated on every check so
    both static middleware constants and live config (monkeypatched settings)
    work. Each check consumes ``units`` toward the window cap.

    The entry table is pruned opportunistically once it grows past
    ``max_keys``, so a churn of unique (or spoofed) client ids cannot grow
    memory unboundedly.
    """

    def __init__(
        self,
        window_s: Callable[[], float],
        max_count: Callable[[], int],
        max_keys: int = 10_000,
    ) -> None:
        self._window_s = window_s
        self._max_count = max_count
        self._max_keys = max_keys
        self._table: Dict[str, WindowEntry] = defaultdict(lambda: (0.0, 0))
        self._lock = Lock()

    @property
    def table(self) -> Dict[str, WindowEntry]:
        """Expose the live window table (tests inspect/age entries)."""
        return self._table

    def check(self, request: Request, units: int = 1) -> bool:
        """Consume ``units`` toward this client's window cap.

        Returns True when the request is allowed, False when it would exceed
        the cap (caller decides the 429 shape)."""
        key = client_key(request)
        window_s = self._window_s()
        max_count = self._max_count()
        with self._lock:
            if key not in self._table and len(self._table) >= self._max_keys:
                # Sweep expired windows once the table is large so idle or
                # spoofed keys cannot grow memory forever.
                cutoff = time.time() - window_s
                for stale in [k for k, (ts, _) in self._table.items() if ts < cutoff]:
                    del self._table[stale]
            window_start, count = self._table[key]
            now = time.time()
            if now - window_start > window_s:
                # Fresh window (first request or expired): still enforce the
                # cap — a max_count of 0 refuses everything.
                if units > max_count:
                    self._table[key] = (now, 0)
                    return False
                self._table[key] = (now, units)
                return True
            if count + units > max_count:
                return False
            self._table[key] = (window_start, count + units)
            return True

    def clear(self) -> None:
        """Drop all tracked windows (tests)."""
        with self._lock:
            self._table.clear()
