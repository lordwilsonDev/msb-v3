"""Studio router — single unified dashboard for sovereign core."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from msb_v3 import __version__
from msb_v3.core.config import settings
from msb_v3.observability.metrics import Metrics

router = APIRouter(tags=["studio"])

_DASHBOARD_HTML = """<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>MSB v3 Studio</title>
  <style>
    :root { font-family: system-ui, sans-serif; background:#0b0d12; color:#e6e8eb; }
    body { margin: 0; padding: 24px; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit,minmax(220px,1fr)); gap: 16px; }
    .card { background:#12161f; border:1px solid #1e2430; border-radius:12px; padding:16px; }
    .muted { color:#8b95a5; }
    .ok { color:#4ade80; } .warn { color:#fbbf24; } .err { color:#f87171; }
  </style>
</head>
<body>
  <h1>MSB v3 Studio</h1>
  <p class="muted">Auto-refresh every 5s. Deep surfaces are linked below.</p>
  <div class="grid" id="panels"></div>
  <script>
    const panels = [
      { label: "Health",   href: "/health",  cls: "ok"   },
      { label: "Ready",    href: "/ready",   cls: "ok"   },
      { label: "Metrics",  href: "/metrics", cls: "warn" },
      { label: "Prom",     href: "/metrics/prometheus", cls: "warn" },
      { label: "Chat",     href: "/chat",    cls: "err"  },
    ];
    const root = document.getElementById("panels");
    panels.forEach(p => {
      root.innerHTML += `<div class="card"><a href="${p.href}" style="color:inherit;text-decoration:none"><div class="${p.cls}">${p.label}</div><div class="muted">${p.href}</div></a></div>`;
    });
  </script>
</body>
</html>"""


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard() -> str:
    return _DASHBOARD_HTML


@router.get("/status")
async def status() -> Dict[str, Any]:
    # Canonical /status (M3 convergence 2026-08-16): absorbed the fields from
    # the duplicate api/status.py router (which was never mounted) and deleted
    # that module — one live status route, no dead copy.
    return {
        "service": "msb-v3",
        "version": __version__,
        "ready": Metrics._ready,
        "model": settings.ollama_model,
        "ollama_url": settings.ollama_url,
        "db_path": settings.db_path,
        "host": settings.host,
        "port": settings.port,
    }
