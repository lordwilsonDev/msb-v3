"""Lightweight home dashboard for msb-v3."""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import httpx
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from msb_v3.core.config import settings

router = APIRouter(tags=["ui"])

# Repo-relative runtime paths (no hardcoded /Users/... homes): the dashboard
# only ever runs from the installed repo, so the root comes from settings
# (MSB_HOME / MSB_REPO env, else derived from the package location).
_RUNTIME_DIR = Path(settings.msb_home) / "runtime"
_RESEARCH_DIR = _RUNTIME_DIR / "research"
_MULCH_DB = _RUNTIME_DIR / "triumvirate" / "mulch_learnings.db"

# The dashboard can be reached through a proxy (e.g. Open WebUI on :3001), so
# relative URLs like /health would resolve against the *proxy* and return its
# HTML -> ValueError. Status probes are always pinned to the real MSB base so
# the dashboard reads the same no matter how it is opened. Loopback, not
# settings.host: the bind address may be a wildcard (0.0.0.0) or an interface
# address, and these probes are always self-fetches on this machine.
_MSB_BASE = f"http://127.0.0.1:{settings.port}"

# Per-probe timeout; monkeypatched by tests to measure parallel vs sequential
# behavior via wall-clock time.
_PROBE_DELAY_S = 8.0


def _list_research_runs() -> list[str]:
    try:
        return sorted([p.name for p in _RESEARCH_DIR.iterdir() if p.is_dir()]) if _RESEARCH_DIR.exists() else []
    except Exception:
        return []


def _get_triumvirate_dashboard() -> dict:
    try:
        from msb_v3.triumvirate.mission_anchor import MissionAnchor
        anchor = MissionAnchor()
        status = anchor.read()
        verify = anchor.verify()
        return {
            "goal": status.get("goal"),
            "phase": status.get("current_phase"),
            "valid": verify.get("valid", False),
            "scope_hash": verify.get("scope_hash"),
            "iteration_count": status.get("iteration_count", 0),
        }
    except Exception:
        return {}


def _get_argus_mulch() -> dict:
    db_path = _MULCH_DB
    try:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                "SELECT id, timestamp, component, finding_type, description, resolution_status FROM mulch_learnings ORDER BY timestamp DESC LIMIT 5"
            ).fetchall()
        return {
            "rows": [
                {
                    "id": r[0],
                    "ts": r[1],
                    "component": r[2],
                    "finding_type": r[3],
                    "description": r[4],
                    "resolution_status": r[5],
                }
                for r in rows
            ]
        }
    except Exception:
        return {"rows": []}


async def _probe(client: httpx.AsyncClient, url: str, *, method: str = "GET", json_body: dict | None = None) -> str:
    """Short status string for one dashboard probe. Async so the probes never
    block the event loop (a sync call here would deadlock: the dashboard
    can't answer the very request it is making). POST-only endpoints
    (telegram) get the right method; a non-JSON response (e.g. a proxy's
    HTML) is reported as INVALID rather than a bare error."""
    try:
        r = await client.request(method, url, json=json_body, timeout=_PROBE_DELAY_S)
        if r.status_code != 200:
            return f"HTTP {r.status_code}"
        if "json" not in r.headers.get("content-type", "") and not r.content[:64].strip().startswith((b"{", b"[")):
            return "INVALID (non-JSON)"
        return "ok"
    except Exception as exc:
        return f"ERR:{type(exc).__name__}"


async def _render_status(client: httpx.AsyncClient, label: str, path: str, *, method: str = "GET", json_body: dict | None = None) -> str:
    status = await _probe(client, _MSB_BASE + path, method=method, json_body=json_body)
    css = "ok" if status == "ok" else "bad"
    return f'<li><a href="{_MSB_BASE + path}">{label}</a>: <span class="{css}">{status}</span></li>'


async def _render_statuses() -> str:
    """All five status probes in parallel — sequential awaits would stack
    their timeouts (5 x 8s worst case = ~40s page load)."""
    async with httpx.AsyncClient() as client:
        lines = await asyncio.gather(
            _render_status(client, "health", "/health"),
            _render_status(client, "preflight", "/research/assistant/preflight"),
            _render_status(client, "safety", "/safety/status"),
            _render_status(client, "evolution", "/evolution/scan"),
            _render_status(
                client, "telegram", "/notify/telegram", method="POST", json_body={"text": "dashboard-health-probe"}
            ),
        )
    return "\n".join(lines)


def _render_triumvirate_status(data: dict) -> str:
    if not data:
        return '<li>Triumvirate: <span class="bad">unavailable</span></li>'
    valid = "ok" if data.get("valid") else "bad"
    phase = data.get("phase") or "unknown"
    goal = data.get("goal") or "none"
    return (
        '<li>Triumvirate: '
        f'<span class="{valid}">{phase}</span> | '
        f'goal=<code>{goal}</code> | '
        f'hash=<code>{data.get("scope_hash", "?")[:12]}</code></li>'
    )


def _render_argus_mulch(data: dict) -> str:
    rows = data.get("rows", []) if data else []
    if not rows:
        return "<li>Argus mulch: <span class='ok'>none</span></li>"
    parts = []
    for row in rows[:3]:
        status_cls = "ok" if row.get("resolution_status") == "resolved" else "bad"
        parts.append(
            f"<li>Argus #{row['id']}: <span class='{status_cls}'>{row['finding_type']}</span> {row['description']}</li>"
        )
    return "\n".join(parts)


@router.get("/", include_in_schema=False)
async def home() -> HTMLResponse:
    items = (
        '<li><a href="/research/assistant/latest">latest</a>: <span class="ok">ready</span></li>'
        + "".join(f"<li>{name}</li>" for name in _list_research_runs()[:20])
    )
    triumph = _get_triumvirate_dashboard()
    items += "\n" + _render_triumvirate_status(triumph)
    items += "\n" + await _render_statuses()
    items += "\n" + _render_argus_mulch(_get_argus_mulch())

    html = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>MSB v3</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background:#0b0c10; color:#c5c6c7; margin:0; padding:2rem; }}
h1 {{ font-weight:300; letter-spacing:0.1em; color:#66fcf1; }}
.ok {{ color:#45a29e; font-weight:600; }}
.bad {{ color:#ff2e63; font-weight:600; }}
a {{ color:#66fcf1; text-decoration:none; }}
ul {{ padding-left:1.2rem; }}
li {{ margin:0.4rem 0; }}
.footer {{ margin-top:2rem; color:#8a8d93; font-size:0.9rem; }}
</style>
</head>
<body>
<h1>MSB v3</h1>
<ul>{items}
</ul>
<div class="footer">Base: /</div>
</body>
</html>"""
    return HTMLResponse(content=html)
