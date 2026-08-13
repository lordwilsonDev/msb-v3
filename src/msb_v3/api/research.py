"""Research assistant router."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

import datetime
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Response
from pydantic import BaseModel

from msb_v3.harnesses.research_assistant import SovereignResearchAssistant

NOTIFY_URL = "https://api.telegram.org/bot{token}/sendMessage"

router = APIRouter(tags=["research"])

# Repo-relative by default (portable across machines/CI); override with
# MSB_RESEARCH_ROOT. Previously a hardcoded absolute machine path, which
# broke every write-path on any other machine (mkdir under /Users fails).
_RESEARCH_ROOT = Path(
    os.environ.get("MSB_RESEARCH_ROOT", str(Path(__file__).resolve().parents[3] / "runtime" / "research"))
)
_RUN_STATE: dict[str, Any] = {"active": None, "queue": [], "history": []}
_CACHE = "max-age=5"


def _cache(response: Response) -> None:
    response.headers["cache-control"] = _CACHE


class RunRequest(BaseModel):
    topic: str
    slug: Optional[str] = None
    sources: List[str] = []


class ReviewRequest(BaseModel):
    decision: str = "pending"
    notes: str = ""


_SAFETY_BLOCKLIST = [
    (re.compile(r"how\s+to\s+(make|build|create)\s+a\s+(bomb|weapon|explosive|malware|ransomware|virus)", re.I), "dangerous/weapon instruction blocked"),
    (re.compile(r"(instruction|guide)\s+to\s+(harm|injure|kill|attack)", re.I), "harm instruction blocked"),
    (re.compile(r"(bypass|disable|hack).+(security|authentication|verification|firewall|antivirus)", re.I), "security bypass blocked"),
]


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
            logger.warning("failed to read telegram token config: %s", exc)
    return ""


def _send_telegram(text: str) -> dict:
    token = _telegram_token()
    chat = str(os.environ.get("HERMES_TELEGRAM_HOME", "8276057240"))
    if not token or not text:
        return {"ok": False, "reason": "missing_token_or_text"}
    payload = json.dumps({"chat_id": chat, "text": text[:4096], "disable_web_page_preview": True}).encode()
    req = urllib.request.Request(NOTIFY_URL.format(token=token), data=payload, headers={"content-type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return {"ok": True, "status": r.status}
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "body": e.read(200).decode(errors="replace")}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def _send_telegram_async(text: str) -> None:
    import asyncio
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, _send_telegram, text)
    except Exception as exc:
        logger.warning("telegram notify failed: %s", exc)


def _safety_check(topic: str) -> dict:
    for pattern, label in _SAFETY_BLOCKLIST:
        if pattern.search(topic):
            return {"allowed": False, "reason": label}
    return {"allowed": True, "reason": ""}


def _runtime_root(slug: str) -> Path:
    return _RESEARCH_ROOT / slug


def _ledger_path(slug: str) -> Path:
    return _runtime_root(slug) / f"{slug}_evidence_ledger.json"


def _state_path(slug: str) -> Path:
    return _runtime_root(slug) / f"{slug}_state.json"


def _write_state(slug: str, status: str, extra: Dict[str, Any] | None = None) -> Path:
    root = _runtime_root(slug)
    root.mkdir(parents=True, exist_ok=True)
    path = _state_path(slug)
    data = {"slug": slug, "status": status, "ts": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    if extra:
        data.update(extra)
    path.write_text(json.dumps(data, indent=2) + "\n")
    return path


def _append_history(slug: str) -> None:
    if slug not in _RUN_STATE["history"]:
        _RUN_STATE["history"].append(slug)


def _complete_run_background(slug: str, topic: str, assistant: SovereignResearchAssistant, result: dict) -> None:
    try:
        _append_history(slug)
        _RUN_STATE["active"] = None
        _write_state(slug, "completed", {"result": result})
        deliver = [
            f"Sovereign research complete: {topic}",
            f"Slug: {assistant.slug}",
            f"Artifacts: {result.get('slug', '')}_*.json|md",
            f"Status: {result.get('status')}",
        ]
        _send_telegram("\n".join(deliver))
    except Exception as exc:
        logger.warning("research delivery/notify failed: %s", exc)


@router.post("/assistant/run")
async def run_research(body: RunRequest, background_tasks: BackgroundTasks) -> dict:
    safety = _safety_check(body.topic)
    if not safety.get("allowed"):
        return {
            "status": "blocked",
            "topic": body.topic,
            "slug": body.slug,
            "reason": safety.get("reason"),
        }
    body.slug = body.slug or re.sub(r"[^a-z0-9]+", "-", body.topic.lower()).strip("-")
    _RUN_STATE["active"] = body.slug
    _RUN_STATE["queue"] = [slug for slug in _RUN_STATE.get("queue", []) if slug != body.slug]
    _write_state(body.slug, "running")
    sources = [Path(p) for p in body.sources if Path(p).exists()]
    assistant = SovereignResearchAssistant(topic=body.topic, slug=body.slug)
    result = assistant.run_full_pipeline(sources=sources)
    background_tasks.add_task(_complete_run_background, body.slug, body.topic, assistant, result)
    result["notify"] = {"ok": True, "async": True}
    return result


@router.get("/assistant/preflight")
async def preflight() -> dict:
    from msb_v3.local_ai.ollama import LocalAIClient
    client = LocalAIClient()
    checks = {"ollama": "unknown"}
    try:
        client.generate("ok", max_tokens=1)
        checks["ollama"] = "ok"
    except Exception as exc:
        checks["ollama"] = f"error: {exc}"
    return {"checks": checks, "passed": checks["ollama"] == "ok", "failed": [k for k, v in checks.items() if v != "ok"]}


@router.get("/assistant/news")
async def ai_news() -> dict:
    news_dir = Path.home() / "projects/ai-news-daily/output"
    try:
        files = sorted(news_dir.glob("*.md"), reverse=True)
        latest = files[0].read_text(errors="replace") if files else "No AI news files found."
    except Exception as exc:
        latest = f"AI news fetch failed: {exc}"
    return {"news": latest, "source": str(news_dir)}


class RalphLoopRunRequest(BaseModel):
    goal: str
    slug: Optional[str] = None
    budget_cap_usd: float = 1.0
    max_loops: int = 12


@router.post("/assistant/ralph-loop")
async def run_ralph_loop(body: RalphLoopRunRequest) -> dict:
    """Execute a Ralph Loop research run with deterministic state guards."""
    from msb_v3.agent.ralph_loop import (
        Constraints,
        Status,
        create_ralph_loop,
        create_research_action,
    )
    from msb_v3.harnesses.base import ChatHarness

    slug = body.slug or re.sub(r"[^a-z0-9]+", "-", body.goal.lower()).strip("-")
    workdir = _RESEARCH_ROOT / f"ralph_{slug}"
    loop = create_ralph_loop(workdir=workdir)

    init_status = Status(
        loop_id=slug,
        status="READY",
        constraints=Constraints(budget_cap_usd=body.budget_cap_usd, max_loops=body.max_loops),
    )
    loop._write_status(init_status)

    # Governance gate: mission/ethics/tools bound before execution
    chat = ChatHarness()
    action_fn = create_research_action(loop, chat_harness=chat)

    result = loop.execute(
        body.goal,
        action_fn=action_fn,
        session=slug,
        mission=body.goal,
        ethics="no_harm; verify_before_act",
        allowed_tools=["local_llm", "filesystem"],
    )
    return {
        "slug": slug,
        "workdir": str(workdir),
        "ok": result.ok,
        "event": result.event,
        "payload": result.payload,
        "telemetry": result.telemetry,
        "error": result.error,
    }


@router.get("/assistant/latest")
async def latest(response: Response) -> dict:
    if not _RESEARCH_ROOT.exists():
        response.headers["cache-control"] = "max-age=5"
        return {"status": "no_artifacts"}
    slugs = sorted(p.name for p in _RESEARCH_ROOT.iterdir() if p.is_dir())
    if not slugs:
        response.headers["cache-control"] = "max-age=5"
        return {"status": "no_artifacts"}
    latest = slugs[-1]
    root = _runtime_root(latest)
    files = sorted(p.name for p in root.iterdir() if p.is_file())
    state = _state_path(latest)
    status = "unknown"
    if state.exists():
        try:
            status = json.loads(state.read_text()).get("status", status)
        except Exception as exc:
            logger.warning("failed to read active run state: %s", exc)
    response.headers["cache-control"] = "max-age=5"
    return {"status": status, "slug": latest, "files": files}


@router.get("/assistant/runs/_active")
async def active_run() -> dict:
    active = _RUN_STATE.get("active")
    state = {}
    if active:
        try:
            state = json.loads(_state_path(active).read_text())
        except Exception:
            state = {"slug": active, "status": "running"}
    return {"active": active, "state": state}


@router.post("/assistant/memory/append")
async def append_memory(body: dict) -> dict:
    return {"ok": True, "received": body}


@router.post("/assistant/runs/{slug}/complete")
async def complete_run(slug: str, body: dict) -> dict:
    text = body.get("text") or f"Research run {slug} marked complete."
    notify = _send_telegram(text)
    return {"status": "ok", "slug": slug, "notify": notify}


@router.get("/assistant/runs")
async def list_runs() -> dict:
    if not _RESEARCH_ROOT.exists():
        return {"runs": []}
    slugs = sorted(p.name for p in _RESEARCH_ROOT.iterdir() if p.is_dir())
    return {"runs": slugs}


@router.get("/assistant/runs/{slug}")
async def get_run(slug: str) -> dict:
    root = _runtime_root(slug)
    if not root.exists():
        return {"slug": slug, "status": "not_found"}
    files = sorted(p.name for p in root.iterdir() if p.is_file())
    state = _state_path(slug)
    status = "unknown"
    if state.exists():
        try:
            status = json.loads(state.read_text()).get("status", status)
        except Exception as exc:
            logger.warning("failed to read run state for %s: %s", slug, exc)
    return {"slug": slug, "status": status, "files": files}


@router.get("/assistant/runs/{slug}/state")
async def run_state(slug: str) -> dict:
    path = _state_path(slug)
    if not path.exists():
        return {"slug": slug, "status": "not_found"}
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {"slug": slug, "status": "unreadable"}
    return data


@router.get("/assistant/runs/{slug}/claims")
async def list_claims(slug: str) -> dict:
    ledger = _ledger_path(slug)
    if not ledger.exists():
        return {"slug": slug, "claims": [], "total": 0}
    try:
        data = json.loads(ledger.read_text())
    except Exception:
        return {"slug": slug, "claims": [], "total": 0, "error": "unreadable_ledger"}
    claims = data.get("claims", [])
    return {"slug": slug, "claims": claims, "total": len(claims)}


@router.post("/assistant/runs/{slug}/claims/review")
async def review_claims(slug: str, body: dict) -> dict:
    notes = body.get("notes", "")
    decision = body.get("decision", "reviewed")
    ledger = _ledger_path(slug)
    if not ledger.exists():
        raise HTTPException(status_code=404, detail="ledger_not_found")
    try:
        data = json.loads(ledger.read_text())
    except Exception:
        raise HTTPException(status_code=500, detail="ledger_unreadable")
    updated = 0
    for claim in data.get("claims", []):
        if claim.get("status") in {"unknown", "pending"}:
            claim["status"] = decision
            claim["review_notes"] = notes
            claim["reviewed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            updated += 1
    ledger.write_text(json.dumps(data, indent=2) + "\n")
    state = _state_path(slug)
    if state.exists():
        try:
            state_data = json.loads(state.read_text())
            state_data["status"] = "reviewed"
            state.write_text(json.dumps(state_data, indent=2) + "\n")
        except Exception as exc:
            logger.warning("failed to mark run reviewed: %s", exc)
    return {"slug": slug, "updated": updated, "decision": decision}


@router.get("/assistant/runs/{slug}/report")
async def get_report(slug: str) -> dict:
    report_path = _runtime_root(slug) / f"{slug}_report.md"
    if not report_path.exists():
        return {"slug": slug, "status": "missing", "markdown": "", "path": str(report_path)}
    content = report_path.read_text(errors="replace")
    return {"slug": slug, "status": "ok", "markdown": content, "path": str(report_path)}


@router.post("/assistant/runs/{slug}/review")
async def review_run(slug: str, body: dict) -> dict:
    decision = body.get("decision", "pending")
    notes = body.get("notes", "")
    state_path = _state_path(slug)
    status = "reviewed"
    if state_path.exists():
        try:
            data = json.loads(state_path.read_text())
            data["status"] = status
            data["review_decision"] = decision
            data["review_notes"] = notes
            data["reviewed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            state_path.write_text(json.dumps(data, indent=2) + "\n")
        except Exception as exc:
            logger.warning("failed to persist review decision: %s", exc)
    return {"slug": slug, "decision": decision, "status": status}


@router.post("/assistant/runs/{slug}/restart")
async def restart_run(slug: str) -> dict:
    topic = slug.replace("-", " ")
    _RUN_STATE["queue"] = [s for s in _RUN_STATE.get("queue", []) if s != slug]
    _RUN_STATE["queue"].append(slug)
    _write_state(slug, "queued", {"topic": topic})
    return {"slug": slug, "status": "queued", "active": _RUN_STATE.get("active")}


@router.post("/assistant/runs/{slug}/cancel")
async def cancel_run(slug: str) -> dict:
    reason = "cancelled"
    _RUN_STATE["queue"] = [s for s in _RUN_STATE.get("queue", []) if s != slug]
    _write_state(slug, "cancelled", {"reason": reason})
    if _RUN_STATE.get("active") == slug:
        _RUN_STATE["active"] = None
    return {"slug": slug, "status": "cancelled", "reason": reason}
