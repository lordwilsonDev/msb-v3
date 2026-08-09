"""Lightweight home dashboard for msb-v3."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["ui"])


def _list_research_runs() -> list[str]:
    run_path = Path("/Users/lordwilson/msb-v3/runtime/research")
    try:
        return sorted([p.name for p in run_path.iterdir() if p.is_dir()]) if run_path.exists() else []
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
    db_path = Path("/Users/lordwilson/msb-v3/runtime/triumvirate/mulch_learnings.db")
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


def _render_status(label: str, url: str) -> str:
    try:
        import urllib.request
        with urllib.request.urlopen(url, timeout=3) as r:
            status = r.status
    except Exception as exc:
        status = f"ERR:{type(exc).__name__}"
    css = "ok" if status == 200 else "bad"
    return f'<li><a href="{url}">{label}</a>: <span class="{css}">{status}</span></li>'


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
    items += "\n" + _render_status("health", "/health")
    items += "\n" + _render_status("preflight", "/research/assistant/preflight")
    items += "\n" + _render_status("safety", "/safety/status")
    items += "\n" + _render_status("evolution", "/evolution/scan")
    items += "\n" + _render_status("telegram", "/notify/telegram")
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
