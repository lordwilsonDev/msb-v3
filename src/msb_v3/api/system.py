"""System router — routes registry + info."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter

from msb_v3.core.config import settings

router = APIRouter(tags=["system"])


@router.get("/info")
def system_info() -> Dict[str, Any]:
    return {"service": "msb-v3", "version": "0.1.0"}


@router.get("/health")
def system_health() -> Dict[str, Any]:
    from msb_v3.db import sqlite as db
    from msb_v3.local_ai.ollama import LocalAIClient
    from msb_v3.observability.metrics import Metrics

    checks: Dict[str, Any] = {"app": "ok", "ready": bool(Metrics._ready)}
    try:
        LocalAIClient().generate("health-check", max_tokens=1)
        checks["ollama"] = "ok"
    except Exception as exc:
        checks["ollama"] = f"error: {exc}"
    try:
        db.healthcheck()
        checks["db"] = "ok"
    except Exception as exc:
        checks["db"] = f"error: {exc}"
    degraded = any(isinstance(v, str) and v.startswith("error:") for v in checks.values())
    checks["status"] = "degraded" if degraded else "healthy"
    return checks


@router.get("/routes")
def list_routes() -> Dict[str, Any]:
    from msb_v3.api.registry import REGISTRY

    # Guarded iteration: a malformed (non-dict) registry entry is skipped
    # rather than raising — /routes is a diagnostic surface and must not 500.
    routes = []
    for e in REGISTRY:
        if isinstance(e, dict):
            routes.append({"prefix": e["prefix"], "tags": e["tags"]})
    return {"routes": routes}


@router.get("/config")
def system_config() -> Dict[str, Any]:
    from msb_v3.flywheel.models import (
        APPROVAL_STAGES,
        ITERATIONS_PER_STAGE,
        RESEARCH_STAGES,
        STAGES,
    )
    from msb_v3.governance.approval import APPROVAL_KINDS
    from msb_v3.observability.metrics import Metrics

    return {
        "service": "msb-v3",
        "version": "0.1.0",
        "host": settings.host,
        "port": settings.port,
        "ollama_url": settings.ollama_url.replace("http://", "").replace("https://", "").split("@")[-1] if settings.ollama_url else "hidden",
        "ollama_model": settings.ollama_model,
        "db_path": settings.db_path,
        "log_level": settings.log_level,
        "cors_origins": settings.cors_origins,
        "request_timeout_s": settings.request_timeout_s,
        # Live guard settings for the /v1 surface — keys are the env-var
        # names so operators can map them 1:1 to .env. Values read live,
        # so a config change applies without a restart (same as the guards).
        "rate_limits": {
            "OPENAI_CHAT_RATE_MAX": settings.openai_chat_rate_max,
            "OPENAI_CHAT_RATE_WINDOW_S": settings.openai_chat_rate_window_s,
            "OPENAI_EMBED_MAX_BATCH": settings.openai_embed_max_batch,
            "OPENAI_EMBED_RATE_MAX": settings.openai_embed_rate_max,
            "OPENAI_EMBED_RATE_WINDOW_S": settings.openai_embed_rate_window_s,
        },
        # Phase 0B brakes — env-var names map 1:1 to .env. Cap semantics:
        # -1 = unlimited, 0 = deny all (fail-closed), >0 = cap. The governor
        # thresholds enforce convergence rather than requesting it.
        "governance": {
            "GOV_BUDGET_RESEARCH_CALLS": settings.gov_budget_research_calls,
            "GOV_BUDGET_TOKENS": settings.gov_budget_tokens,
            "GOV_BUDGET_ITERATIONS": settings.gov_budget_iterations,
            "GOV_BUDGET_WINDOW_MIN": settings.gov_budget_window_min,
            "GOV_GOVERNOR_STALL_LIMIT": settings.gov_governor_stall_limit,
            "GOV_GOVERNOR_NOVELTY_MIN": settings.gov_governor_novelty_min,
            "GOV_GOVERNOR_DUP_RATIO_HALT": settings.gov_governor_dup_ratio_halt,
            "GOV_GOVERNOR_HISTORY": settings.gov_governor_history,
        },
        # Approval policy — which flywheel stages need an operator decision,
        # and which approval-queue kind gates each. Constants, not env-tunable.
        # Live queue state (pending counts, decisions) lives on /governance/status.
        "approvals": {
            "kinds_requiring_approval": list(APPROVAL_KINDS),
            "stages_requiring_approval": APPROVAL_STAGES,
        },
        # Flywheel loop mechanics (constants).
        "flywheel": {
            "stages": list(STAGES),
            "iterations_per_stage": ITERATIONS_PER_STAGE,
            "research_stages": list(RESEARCH_STAGES),
        },
        "ready": Metrics._ready,
    }
