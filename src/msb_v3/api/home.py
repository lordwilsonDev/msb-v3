"""Lightweight home dashboard for msb-v3."""
from __future__ import annotations

import concurrent.futures
import urllib.error
import urllib.request
from typing import Dict, Tuple

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["ui"])


def _fetch_status(url: str) -> Tuple[str, str]:
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=1) as r:
            return (url, str(r.status))
    except urllib.error.HTTPError as exc:
        return (url, str(exc.code))
    except Exception as exc:
        return (url, f"ERR:{exc}")


@router.get("/", include_in_schema=False)
async def home() -> HTMLResponse:
    base = "http://127.0.0.1:8766"
    endpoints: Dict[str, str] = {
        "health": f"{base}/health",
        "preflight": f"{base}/research/assistant/preflight",
        "safety": f"{base}/safety/status",
        "evolution": f"{base}/evolution/scan",
        "notify": f"{base}/notify/telegram",
    }
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        futures = {name: pool.submit(_fetch_status, url) for name, url in endpoints.items()}
        statuses = {name: future.result(timeout=2) for name, future in futures.items()}

    # home route itself: `/notify/telegram` is POST-only in the API,
    # report 405 for the snapshot instead of hanging.
    normalized = {}
    for name, (url, status) in statuses.items():
        if name == "notify" and status == "405":
            status = "POST:405"
        normalized[name] = (url, status)

    items = []
    for name, (url, status) in normalized.items():
        css = "ok" if status.startswith("200") else "bad"
        items.append(f'<li><a href="{url}">{name}</a>: <span class="{css}">{status}</span></li>')

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
<ul>{"".join(items)}</ul>
<div class="footer">Base: <a href="{base}">{base}</a></div>
</body>
</html>"""
    return HTMLResponse(content=html)
