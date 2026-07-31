"""Lightweight home dashboard for msb-v3."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["ui"])


@router.get("/", include_in_schema=False)
async def home() -> HTMLResponse:
    try:
        run_path = Path("/Users/lordwilson/msb-v3/runtime/research")
        runs = sorted([p.name for p in run_path.iterdir() if p.is_dir()]) if run_path.exists() else []
    except Exception:
        runs = []

    items = (
        f'<li><a href="/research/assistant/latest">latest</a>: <span class="ok">ready</span></li>'
        + "".join(f"<li>{name}</li>" for name in runs[:20])
    )

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
<li><a href="/health">health</a>: <span class="ok">/health</span></li>
<li><a href="/research/assistant/preflight">preflight</a>: <span class="ok">/research/assistant/preflight</span></li>
<li><a href="/safety/status">safety</a>: <span class="ok">/safety/status</span></li>
<li><a href="/evolution/scan">evolution</a>: <span class="ok">/evolution/scan</span></li>
<li>notify: <span class="ok">/notify/telegram [POST]</span></li>
</ul>
<div class="footer">Base: /</div>
</body>
</html>"""
    return HTMLResponse(content=html)
