"""Shared guard-config surface — one builder for /system/config and the
operator CLIs (governance + flywheel).

Single source of truth for the operator-visible guard configuration:
the /v1 rate-limit guards, the Phase 0B brakes (budget caps + governor
thresholds), the approval policy, and the flywheel loop mechanics.

Values are read live from settings at call time so a .env change applies
without a restart (same as the guards themselves). Policy constants are
imported directly from the enforcement modules, so this surface cannot
drift from the code that enforces it.
"""

from __future__ import annotations

from typing import Any, Dict

from msb_v3.core.config import settings


def guard_config() -> Dict[str, Any]:
    """Return the guard/brake/approval/flywheel config blocks.

    Keys are the exact env-var names for settings-backed values so
    operators can map them 1:1 to .env; constants use semantic names.
    """
    from msb_v3.flywheel.models import (
        APPROVAL_STAGES,
        ITERATIONS_PER_STAGE,
        RESEARCH_STAGES,
        STAGES,
    )
    from msb_v3.governance.approval import APPROVAL_KINDS

    return {
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
    }


def render_human(cfg: Dict[str, Any]) -> str:
    """Human-readable rendering of the guard-config blocks.

    Shared by the governance and flywheel CLIs so both operator consoles
    print the same lines — no presentation drift between the two surfaces.
    """
    gov = cfg["governance"]
    window_min = gov["GOV_BUDGET_WINDOW_MIN"]
    kinds = ", ".join(cfg["approvals"]["kinds_requiring_approval"])
    stages = ", ".join(
        f"{s}->{k}" for s, k in cfg["approvals"]["stages_requiring_approval"].items()
    )
    fw = cfg["flywheel"]
    rl = cfg["rate_limits"]
    lines = [
        "[governance] budget caps per rolling window:",
        f"  research_calls: {gov['GOV_BUDGET_RESEARCH_CALLS']}  (window {window_min}m)",
        f"  tokens: {gov['GOV_BUDGET_TOKENS']}  (window {window_min}m)",
        f"  iterations: {gov['GOV_BUDGET_ITERATIONS']}  (window {window_min}m)",
        "[governance] governor thresholds: "
        f"stall_limit={gov['GOV_GOVERNOR_STALL_LIMIT']} "
        f"novelty_min={gov['GOV_GOVERNOR_NOVELTY_MIN']} "
        f"dup_ratio_halt={gov['GOV_GOVERNOR_DUP_RATIO_HALT']} "
        f"history={gov['GOV_GOVERNOR_HISTORY']}",
        f"[governance] approval kinds: {kinds}",
        f"[governance] approval stages: {stages}",
        f"[flywheel] stages ({len(fw['stages'])}): {', '.join(fw['stages'])}",
        f"[flywheel] iterations per stage: {fw['iterations_per_stage']}",
        f"[flywheel] research-call spenders: {', '.join(fw['research_stages'])}",
        "[rate] chat: "
        f"{rl['OPENAI_CHAT_RATE_MAX']} req / {rl['OPENAI_CHAT_RATE_WINDOW_S']}s; "
        "embed: "
        f"{rl['OPENAI_EMBED_RATE_MAX']} items / {rl['OPENAI_EMBED_RATE_WINDOW_S']}s, "
        f"max batch {rl['OPENAI_EMBED_MAX_BATCH']}",
    ]
    return "\n".join(lines) + "\n"
