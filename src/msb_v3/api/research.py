"""Research assistant router."""
from __future__ import annotations

import datetime
import json
import os
import urllib.request
import urllib.error
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel
from typing import List, Optional

from msb_v3.harnesses.research_assistant import SovereignResearchAssistant

NOTIFY_URL = "https://api.telegram.org/bot{token}/sendMessage"

router = APIRouter(tags=["research"])


class RunRequest(BaseModel):
    topic: str
    slug: Optional[str] = None
    sources: List[str] = []


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
        except Exception:
            pass
    return ""


def _send_telegram(text: str) -> dict:
    token = _telegram_token()
    chat = str(os.environ.get("HERMES_TELEGRAM_HOME", "8276057240"))
    if not token or not text:
        return {"ok": False, "reason": "missing_token_or_text"}
    payload = json.dumps({"chat_id": chat, "text": text[:4096], "disable_web_page_preview": True}).encode()
    req = urllib.request.Request(NOTIFY_URL.format(token=token), data=payload, headers={"content-type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return {"ok": True, "status": r.status}
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "body": e.read(200).decode(errors="replace")}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.post("/assistant/run")
async def run_research(body: RunRequest) -> dict:
    sources = [Path(p) for p in body.sources if Path(p).exists()]
    assistant = SovereignResearchAssistant(topic=body.topic, slug=body.slug)
    result = assistant.run_full_pipeline(sources=sources)
    deliver = [
        f"Sovereign research complete: {body.topic}",
        f"Slug: {assistant.slug}",
        f"Artifacts: {result.get('slug', '')}_*.json|md",
        f"Status: {result.get('status')}",
    ]
    notify = _send_telegram("\n".join(deliver))
    result["notify"] = notify
    return result


@router.get("/assistant/preflight")
async def preflight() -> dict:
    from msb_v3.local_ai.ollama import LocalAIClient
    client = LocalAIClient()
    checks = {"ollama": "unknown"}
    try:
        client.generate("ok", max_tokens=1)
        checks["ollama"] = "ok"
    except Exception as exc:  # pragma: no cover
        checks["ollama"] = f"error: {exc}"
    return {"checks": checks, "passed": checks["ollama"] == "ok", "failed": [k for k, v in checks.items() if v != "ok"]}


@router.get("/assistant/latest")
async def latest() -> dict:
    root = Path("/Users/lordwilson/msb-v3/runtime/research")
    latest_dir = root / "sovereign-ai-orchestration"
    if not latest_dir.exists():
        return {"status": "no_artifacts"}
    files = sorted(p.name for p in latest_dir.iterdir() if p.is_file())
    state = latest_dir / f"sovereign-ai-orchestration_state.json"
    status = json.loads(state.read_text())["status"] if state.exists() else "unknown"
    return {"status": status, "slug": "sovereign-ai-orchestration", "files": files}


@router.get("/assistant/state")
async def assistant_state() -> dict:
    return {"status": "idle", "active_run": None}


@router.post("/assistant/runs/{slug}/complete")
async def complete_run(slug: str, body: dict) -> dict:
    text = body.get("text") or f"Research run {slug} marked complete."
    notify = _send_telegram(text)
    return {"status": "ok", "slug": slug, "notify": notify}


@router.get("/assistant/runs")
async def list_runs() -> dict:
    root = Path("/Users/lordwilson/msb-v3/runtime/research")
    if not root.exists():
        return {"runs": []}
    slugs = sorted(p.name for p in root.iterdir() if p.is_dir())
    return {"runs": slugs}


@router.get("/assistant/runs/{slug}")
async def get_run(slug: str) -> dict:
    root = Path("/Users/lordwilson/msb-v3/runtime/research") / slug
    if not root.exists():
        return {"slug": slug, "status": "not_found"}
    files = sorted(p.name for p in root.iterdir() if p.is_file())
    return {"slug": slug, "status": "found", "files": files}


@router.post("/assistant/memory/append")
async def append_memory(body: dict) -> dict:
    return {"ok": True, "received": body}


@router.post("/assistant/runs/{slug}/review")
async def review_run(slug: str, body: dict) -> dict:
    return {"slug": slug, "decision": body.get("decision", "pending"), "notes": body.get("notes", "")}
