"""The automation brain — plan, then create.

``plan_automation`` turns free text into a structured plan via DeepSeek (the
$10 brain; injectable for tests). ``create_automation`` executes the plan
through the provider clients under the runtime's discipline:

- **Dry-run by default** — ``approve=False`` (or ``MSB_AUTOMATION_DRY_RUN=1``)
  records a ``dry_run`` manifest entry and creates nothing. Creation with
  side effects requires ``approve=True`` (the operator token IS the
  approval, same rule as the cron ``requires_approval`` jobs).
- **Budget cap** — the LLM brain spend is recorded against
  ``MSB_AUTOMATION_BUDGET_USD``; a creation that would exceed the cap is
  refused (fail-closed).
- **Manifest ledger** — every attempt lands in the manifest with its status,
  so the operator sees created / dry_run / blocked / failed and why.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, Dict, List, Optional

from msb_v3.automation.budget import (
    CREATE_ESTIMATE_USD,
    PLAN_ESTIMATE_USD,
    BudgetLedger,
)
from msb_v3.automation.clients import PROVIDERS, get_client
from msb_v3.automation.manifest import Manifest
from msb_v3.automation.state import AutomationState

logger = logging.getLogger(__name__)

LlmFn = Callable[[List[Dict[str, Any]]], str]

_PLAN_SYSTEM = (
    "You translate automation requests into a single structured plan. Reply with "
    "exactly one fenced JSON block, nothing else:\n"
    '```json\n{"automation": {"provider": "n8n|make|zapier|ghl", "name": "<short '
    'name>", "description": "<what it does, one sentence>"}}\n```\n'
    "Pick the provider from the request when named; otherwise prefer n8n (self-"
    "hosted, free). n8n = a webhook workflow. make = a Make scenario webhook. "
    "zapier = a Zapier webhook. ghl = a GoHighLevel workflow. Never invent "
    "details beyond name + description."
)


def default_llm() -> LlmFn:
    from msb_v3.local_ai.deepseek import DeepSeekClient

    client = DeepSeekClient(timeout_s=45.0)

    def llm(messages: List[Dict[str, Any]]) -> str:
        return client.chat(messages, temperature=0.2, max_tokens=600).text

    return llm


def plan_automation(text: str, llm: Optional[LlmFn] = None) -> Dict[str, Any]:
    """Turn a request into a validated automation plan. Recipes parse
    deterministically first (free, zero spend — see recipes.py); anything
    else goes to the LLM. Raises ValueError on an unparseable /
    unactionable request."""
    if not (text or "").strip():
        raise ValueError("automation request text is required")
    from msb_v3.automation.recipes import parse as parse_recipe

    recipe = parse_recipe(text)
    if recipe is not None:
        return recipe
    fn = llm if llm is not None else default_llm()
    out = fn(
        [
            {"role": "system", "content": _PLAN_SYSTEM},
            {"role": "user", "content": (text or "")[:2000]},
        ]
    )
    plan = try_parse_plan(out)
    if plan is None:
        raise ValueError("could not parse an automation plan from the model output")
    return plan


def try_parse_plan(text: str) -> Optional[Dict[str, Any]]:
    """Extract + validate a fenced JSON automation block."""
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text or "", re.DOTALL)
    if match is None:
        return None
    import json

    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    auto = data.get("automation") if isinstance(data, dict) else None
    if not isinstance(auto, dict):
        return None
    provider = str(auto.get("provider", "")).strip().lower()
    name = str(auto.get("name", "")).strip()
    description = str(auto.get("description", "")).strip()
    if provider not in PROVIDERS or not name or not description:
        return None
    return {"provider": provider, "name": name[:80], "description": description[:300]}


def create_automation(
    plan: Dict[str, Any],
    approve: bool = False,
    *,
    budget: Optional[BudgetLedger] = None,
    manifest: Optional[Manifest] = None,
    state: Any = None,
    client_factory: Optional[Callable[[str], Any]] = None,
) -> Dict[str, Any]:
    """Execute a validated plan under the runtime's discipline. Returns a
    result dict with ``status`` in created / dry_run / blocked / failed.

    Two paths: a *living* plan (provider="self" with schedule+action — a
    recipe) registers with the dispatcher state, no platform client, zero
    spend; a provider plan (n8n/make/zapier/ghl) creates on the platform.
    ``client_factory``/``state`` are injectable for tests.
    """
    provider = str(plan.get("provider", "")).strip().lower()
    name = str(plan.get("name", "")).strip()
    description = str(plan.get("description", "")).strip()
    manifest = manifest if manifest is not None else Manifest()

    # --- Living-automation path (recipes / provider="self") ---
    # The dispatcher executes it on the wake cycle — the manifest entry +
    # state row ARE the automation. Deterministic by construction: no LLM,
    # no budget spend, no platform syntax.
    schedule = plan.get("schedule")
    action = plan.get("action")
    if provider == "self" and isinstance(schedule, str) and isinstance(action, dict):
        if not approve:
            entry = manifest.append(
                provider=provider, name=name, description=description,
                status="dry_run", summary=f"would register living automation '{name}' ({schedule})",
                detail={"plan": plan}, schedule=schedule, action=action,
            )
            return {
                "ok": True, "status": "dry_run",
                "summary": f"recipe ready — would register '{name}' on {schedule} (approve to enable)",
                "plan": plan, "entry": entry,
            }
        try:
            from msb_v3.cron.parser import CronExpr

            CronExpr.parse(schedule)
        except ValueError as exc:
            entry = manifest.append(
                provider=provider, name=name, description=description,
                status="failed", summary=f"invalid schedule: {exc}", detail={"plan": plan},
            )
            return {"ok": False, "status": "failed", "summary": f"invalid schedule: {exc}", "entry": entry}
        state_store = state if state is not None else AutomationState()
        entry = manifest.append(
            provider=provider, name=name, description=description,
            status="created", summary=f"living automation registered ({schedule})",
            detail={"plan": plan}, schedule=schedule, action=action,
        )
        state_store.upsert(entry["id"], schedule, enabled=True)
        return {
            "ok": True, "status": "created",
            "summary": f"registered living automation '{name}' on {schedule} — the wake cycle dispatches it",
            "entry": entry,
        }

    if provider not in PROVIDERS:
        raise ValueError(f"unknown automation provider: {provider!r}")
    budget = budget if budget is not None else BudgetLedger()
    make_client = client_factory if client_factory is not None else get_client

    # LLM brain spend: record the plan + creation estimates (fail-closed if
    # the cap is hit — the brain never overspends).
    try:
        budget.record(PLAN_ESTIMATE_USD, kind="llm_plan", provider=provider, name=name)
        spend = PLAN_ESTIMATE_USD + CREATE_ESTIMATE_USD
        if approve:
            budget.record(CREATE_ESTIMATE_USD, kind="llm_create", provider=provider, name=name)
    except ValueError as exc:
        entry = manifest.append(
            provider=provider, name=name, description=description,
            status="blocked", summary=f"budget: {exc}", detail={"budget": budget.status()},
        )
        return {"ok": False, "status": "blocked", "summary": f"budget: {exc}", "entry": entry, "budget": budget.status()}

    # Dry-run answers "what would you do" and must not depend on credentials:
    # the approval step is where a missing key surfaces as blocked.
    if not approve:
        entry = manifest.append(
            provider=provider, name=name, description=description,
            status="dry_run", summary=f"would create via {provider} (approve required)",
            detail={"plan": plan, "budget_usd": round(spend, 4)},
        )
        return {
            "ok": True,
            "status": "dry_run",
            "summary": f"plan ready — would create '{name}' via {provider} (approve to execute)",
            "plan": plan,
            "entry": entry,
            "budget": budget.status(),
        }

    client = make_client(provider)
    if not client.available():
        entry = manifest.append(
            provider=provider, name=name, description=description,
            status="blocked", summary=client.unavailable_reason(),
            detail={"budget_usd": round(spend, 4)},
        )
        return {
            "ok": False,
            "status": "blocked",
            "summary": client.unavailable_reason(),
            "entry": entry,
            "budget": budget.status(),
        }

    try:
        detail = _execute(client, provider, name, description)
    except Exception as exc:  # noqa: BLE001 — a client failure is a manifest entry, not a crash
        logger.exception("automation creation failed for %s/%s", provider, name)
        entry = manifest.append(
            provider=provider, name=name, description=description,
            status="failed", summary=f"{type(exc).__name__}: {exc}", detail={"budget_usd": round(spend, 4)},
        )
        return {"ok": False, "status": "failed", "summary": f"{type(exc).__name__}: {exc}", "entry": entry, "budget": budget.status()}

    entry = manifest.append(
        provider=provider, name=name, description=description,
        status="created", summary=f"created via {provider}", detail=detail,
    )
    return {"ok": True, "status": "created", "summary": f"created '{name}' via {provider}", "entry": entry, "detail": detail, "budget": budget.status()}


def _execute(client: Any, provider: str, name: str, description: str) -> Dict[str, Any]:
    """Create the automation on the provider. n8n is fully real (workflow +
    activation + webhook URL); the others return their scaffold/API result."""
    if provider == "n8n":
        from msb_v3.automation.clients import build_n8n_webhook_workflow

        workflow = build_n8n_webhook_workflow(name, description)
        created = client.create_workflow(workflow)
        wf_id = str(created.get("id", ""))
        path = next(
            (str(n.get("parameters", {}).get("path", "")) for n in workflow["nodes"] if n.get("type") == "n8n-nodes-base.webhook"),
            "",
        )
        activated: Dict[str, Any] = {}
        if wf_id:
            try:
                activated = client.set_active(wf_id, active=True)
            except Exception as exc:  # noqa: BLE001 — creation succeeded; activation failure is reported, not fatal
                activated = {"activation_error": f"{type(exc).__name__}: {exc}"}
        return {
            "workflow_id": wf_id,
            "webhook_url": client.webhook_url(path) if path else "",
            "active": bool(activated.get("active", False)),
            "n8n_response": {k: created.get(k) for k in ("id", "name", "active") if k in created},
            **activated,
        }
    return client.create_workflow(name, description)
