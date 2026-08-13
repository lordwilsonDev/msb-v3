"""Notify router with Telegram delivery."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from fastapi import APIRouter

router = APIRouter(tags=["notify"])


def _telegram_token() -> str:
    token = os.environ.get("HERMES_TELEGRAM_BOT_TOKEN", "")
    if token:
        return token
    cfg = Path.home().joinpath(".hermes/config.yaml")
    if cfg.exists():
        try:
            txt = cfg.read_text(errors="replace")
            for line in txt.splitlines():
                if "token:" in line and "bot" not in line.lower():
                    return line.split("token:", 1)[1].strip().strip("\"'[] ") or ""
        except Exception as exc:
            logger.warning("failed to read notify token config: %s", exc)
    return ""


@router.post("/telegram")
async def notify_telegram(body: dict) -> dict:
    token = _telegram_token()
    chat = str(body.get("chat") or os.environ.get("HERMES_TELEGRAM_HOME", "8276057240"))
    text = str(body.get("text", ""))[:4096]
    if not token or not text:
        return {"ok": False, "reason": "missing_token_or_text"}
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({"chat_id": chat, "text": text, "disable_web_page_preview": True}).encode()
    req = urllib.request.Request(url, data=payload, headers={"content-type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return {"ok": True, "status": r.status}
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "body": e.read(200).decode(errors="replace")}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
