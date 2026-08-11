"""Tests for the MSB / dashboard (msb_v3.api.home).

The dashboard self-probes five endpoints to render its status lines. These
tests pin the two properties that matter:

1. **Parallelism** — the probes must run concurrently (asyncio.gather), not
   sequentially. Five probes that each take 0.3s must finish in ~0.3s total,
   not ~1.5s (the old sequential version stacked timeouts up to ~40s worst
   case on a page load).
2. **Isolation** — one probe failing must not affect the others, and the page
   must still render.

Probes are mocked so no real server round-trip happens.
"""

from __future__ import annotations

import asyncio
import re
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from msb_v3.api import home as home_mod  # noqa: E402

_LABELS = ["health", "preflight", "safety", "evolution", "telegram"]


async def _slow_ok_probe(client, url: str, **kwargs) -> str:
    """Each probe takes PROBE_DELAY seconds; returns 'ok' so timing isolates
    the parallel-vs-sequential behavior."""
    await asyncio.sleep(home_mod._PROBE_DELAY_S)
    return "ok"


@pytest.mark.asyncio
async def test_probes_run_in_parallel(monkeypatch) -> None:
    """Five 0.3s probes must finish in ~0.3s, not ~1.5s."""
    monkeypatch.setattr(home_mod, "_PROBE_DELAY_S", 0.3)
    monkeypatch.setattr(home_mod, "_probe", _slow_ok_probe)

    start = time.monotonic()
    html = await home_mod._render_statuses()
    elapsed = time.monotonic() - start

    # All five lines present and 'ok'.
    for label in _LABELS:
        assert f'>{label}</a>' in html
    assert html.count(">ok</span>") == 5

    # Parallel: ~0.3s. Sequential would be ~1.5s. Generous bound (1.0s) keeps
    # the test robust on slow CI boxes while still failing a sequential stack.
    assert elapsed < 1.0, f"probes took {elapsed:.2f}s — expected parallel (~0.3s), got sequential"


@pytest.mark.asyncio
async def test_one_failing_probe_does_not_break_the_others(monkeypatch) -> None:
    """A failing probe renders its own error label; the other four still say ok."""
    calls: dict[str, int] = {}

    async def flaky_probe(client, url: str, **kwargs) -> str:
        await asyncio.sleep(0.01)
        label = url.rsplit("/", 1)[-1]
        calls[label] = calls.get(label, 0) + 1
        if label == "telegram":
            return "ERR:TimeoutError"
        return "ok"

    monkeypatch.setattr(home_mod, "_probe", flaky_probe)
    html = await home_mod._render_statuses()

    assert "ERR:TimeoutError" in html
    assert html.count(">ok</span>") == 4  # the other four unaffected
    assert calls["telegram"] == 1  # failure is not retried by its siblings


@pytest.mark.asyncio
async def test_non_json_probe_response_reported_as_invalid(monkeypatch) -> None:
    async def html_probe(client, url: str, **kwargs) -> str:
        return "INVALID (non-JSON)"

    monkeypatch.setattr(home_mod, "_probe", html_probe)
    html = await home_mod._render_statuses()
    assert html.count("INVALID (non-JSON)") == 5
    # INVALID renders as bad, not as a bare exception
    assert "ValueError" not in html


@pytest.mark.asyncio
async def test_telegram_probe_uses_post_with_body(monkeypatch) -> None:
    seen: dict = {}

    async def capture_probe(client, url: str, *, method: str = "GET", json_body=None) -> str:
        seen["method"] = method
        seen["body"] = json_body
        return "ok"

    monkeypatch.setattr(home_mod, "_probe", capture_probe)
    await home_mod._render_statuses()
    assert seen["method"] == "POST"
    assert seen["body"] == {"text": "dashboard-health-probe"}


@pytest.mark.asyncio
async def test_full_dashboard_renders_with_all_sections(monkeypatch) -> None:
    """End-to-end page render with probes mocked: session list, Triumvirate
    line, all five status lines, and the Argus mulch footer."""
    monkeypatch.setattr(home_mod, "_probe", _slow_ok_probe)
    monkeypatch.setattr(home_mod, "_PROBE_DELAY_S", 0.01)  # keep the full render fast
    monkeypatch.setattr(home_mod, "_list_research_runs", lambda: ["run-a", "run-b"])
    monkeypatch.setattr(
        home_mod,
        "_get_triumvirate_dashboard",
        lambda: {"goal": "g", "phase": "locked", "valid": True, "scope_hash": "d96c8559b768", "iteration_count": 1},
    )
    monkeypatch.setattr(home_mod, "_get_argus_mulch", lambda: {"rows": []})

    html = await home_mod.home()
    assert html.status_code == 200
    body = html.body.decode()

    assert "<h1>MSB v3</h1>" in body
    assert "latest</a>: <span class=\"ok\">ready</span>" in body
    assert "run-a" in body and "run-b" in body
    assert "locked</span>" in body and "d96c8559b768" in body
    for label in _LABELS:
        assert re.search(rf'>{label}</a>.*?class="ok"', body, re.S)
    assert "Argus mulch: <span class='ok'>none</span>" in body
