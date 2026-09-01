"""The wake cycle — one governed pass over the wake inbox.

``run_wake_cycle`` is the body of the ``wake_agent`` cron action. It is
synchronous (cron actions are sync callables run under the scheduler's kill
switch / timeout / retries) and bounded: at most ``MSB_WAKE_MAX_PER_RUN``
pending messages per cycle, each turn capped by the client timeout.

The default turn function drives DeepSeek (the $10 brain); tests inject a
stub. After each turn the response is scanned for a fenced JSON automation
plan (``{\"automation\": {...}}``) and handed to the automation brain, which
dry-runs by default — so a wake message like \"build me an n8n workflow that
pings me\" produces a plan + dry-run manifest entry, not an untested
workflow.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional

from msb_v3.core.config import settings
from msb_v3.wake.store import WakeStore

logger = logging.getLogger(__name__)

TurnFn = Callable[[str, str], str]

_WAKE_SYSTEM = (
    "You are the resident agent of msb-v3, Wilson's sovereign AI runtime. A message "
    "was left for you in the wake inbox. Respond directly, peer-to-peer, concisely "
    "(a few sentences — no fluff, no ceremony).\n\n"
    "If the message asks you to BUILD, CREATE, or SET UP an automation (n8n, Make, "
    "Zapier, or GoHighLevel workflow), end your response with exactly one fenced "
    "JSON block:\n"
    '```json\n{"automation": {"provider": "n8n|make|zapier|ghl", "name": "<short '
    'name>", "description": "<what it does, one sentence>"}}\n```\n'
    "Never invent a provider or a workflow you cannot describe. If the message is "
    "not an automation request, do not emit the JSON block."
)


def default_turn_fn() -> TurnFn:
    """DeepSeek-backed turn (the $10 brain), with a local Ollama fallback.

    When the DeepSeek call fails for any reason (key unset, HTTP 402 /
    payment required, circuit open, timeout, connection error) and
    ``settings.wake_allow_local_fallback`` is on, the same turn is retried
    against the local model so a provider outage degrades the resident loop
    instead of stopping it. If the fallback is disabled or also fails, the
    original error propagates and the runner marks the message failed.
    """
    from msb_v3.local_ai.deepseek import DeepSeekClient

    client = DeepSeekClient(timeout_s=45.0)

    def _messages(text: str, sender: str) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": _WAKE_SYSTEM},
            {"role": "user", "content": f"[from {sender}]\n{text}"},
        ]

    def _local_turn(text: str, sender: str) -> str:
        from msb_v3.local_ai.ollama import LocalAIClient

        resp = LocalAIClient().chat(_messages(text, sender), temperature=0.4, max_tokens=1024)
        return resp.text

    def turn(text: str, sender: str) -> str:
        try:
            resp = client.chat(_messages(text, sender), temperature=0.4, max_tokens=1024)
            return resp.text
        except Exception as exc:  # noqa: BLE001 — any DeepSeek failure is a fallback trigger
            if not settings.wake_allow_local_fallback:
                raise
            logger.warning("wake: DeepSeek turn failed (%s) — falling back to local model", exc)
            return _local_turn(text, sender)

    return turn


def run_wake_cycle(
    *,
    store: Optional[WakeStore] = None,
    max_items: Optional[int] = None,
    turn_fn: Optional[TurnFn] = None,
    dispatcher_fn: Optional[Callable[[], Dict[str, Any]]] = None,
    audit_fn: Optional[Callable[[], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """One governed wake pass — the single heartbeat of the resident agent.

    Always runs three legs, in order: the inbox (up to ``max_items`` pending
    messages, each turn capped), the dispatcher (due *living* automations),
    and the self-maintenance audit. Returns the action-result shape the cron
    scheduler expects ({\"ok\", \"summary\", \"detail\"}).

    ``dispatcher_fn``/``audit_fn`` are injectable for tests; defaults are the
    real dispatcher and audit, so the wake cycle is the clock for
    everything — messages, automations, and self-healing."""
    store = store or WakeStore()
    limit = int(max_items or settings.wake_max_per_run)
    turn = turn_fn or default_turn_fn()
    dispatch = dispatcher_fn or default_dispatcher
    audit = audit_fn or default_audit

    inbox = _process_inbox(store, limit, turn)
    dispatch_result = dispatch()
    audit_result = audit()

    summary = (
        f"wake cycle: {inbox['summary']}; {dispatch_result.get('summary', '')}; "
        f"{audit_result.get('summary', '')}"
    )
    return {
        "ok": True,
        "summary": summary,
        "detail": {
            "processed": inbox["processed"],
            "failed": inbox["failed"],
            "pending_remaining": inbox["pending_remaining"],
            "dispatch": dispatch_result.get("detail", {}),
            "audit": audit_result.get("findings", []),
            "audit_changed": bool(audit_result.get("changed", False)),
        },
    }


def default_dispatcher() -> Dict[str, Any]:
    """The real dispatcher leg — module-level so tests can monkeypatch it."""
    from msb_v3.automation.dispatcher import run_due

    return run_due()


def default_audit() -> Dict[str, Any]:
    """The real self-maintenance leg — module-level so tests can monkeypatch it."""
    from msb_v3.automation.audit import run_audit

    return run_audit()


def _process_inbox(store: WakeStore, limit: int, turn: TurnFn) -> Dict[str, Any]:
    """Process up to ``limit`` pending messages; never aborts on one bad
    message (it is marked failed with its error, kept visible)."""
    pending = store.pending(limit=limit)
    if not pending:
        return {"summary": "inbox empty", "processed": [], "failed": [], "pending_remaining": 0}

    processed: List[str] = []
    failed: List[Dict[str, str]] = []
    for msg in pending:
        try:
            reply = turn(msg["text"], msg["sender"])
        except Exception as exc:  # noqa: BLE001 — one bad message must not abort the cycle
            store.mark_failed(msg["id"], f"{type(exc).__name__}: {exc}")
            failed.append({"id": msg["id"], "error": f"{type(exc).__name__}: {exc}"})
            logger.warning("wake turn failed for %s: %s", msg["id"], exc)
            continue
        automation_note = _automation_hook(reply)
        out = store.respond(msg["id"], reply + automation_note)
        processed.append(out["in_reply_to"])
    remaining = store.pending_count()
    return {
        "summary": f"{len(processed)} processed, {len(failed)} failed, {remaining} remaining",
        "processed": processed,
        "failed": failed,
        "pending_remaining": remaining,
    }


def _automation_hook(reply: str) -> str:
    """If the wake turn emitted an automation plan, route it to the brain.
    The brain dry-runs by default, so this never creates anything untested;
    the note appended to the outbox reply carries the result."""
    plan = _try_parse_plan(reply)
    if plan is None:
        return ""
    try:
        from msb_v3.automation.brain import create_automation

        result = create_automation(plan, approve=False)
        return f"\n[automation] {result.get('status')}: {result.get('summary', '')}"
    except Exception as exc:  # noqa: BLE001 — the reply is still delivered
        logger.warning("automation hook failed: %s", exc)
        return f"\n[automation] error: {type(exc).__name__}: {exc}"


def _try_parse_plan(text: str) -> Optional[Dict[str, Any]]:
    """Extract a fenced JSON automation block from a response. Returns the
    validated plan dict or None."""
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text or "", re.DOTALL)
    if match is None:
        return None
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
    if provider not in ("n8n", "make", "zapier", "ghl") or not name or not description:
        return None
    return {"provider": provider, "name": name[:80], "description": description[:300]}


def ensure_wake_job(cron_store: Any = None) -> bool:
    """Seed the wake-agent cron job (schedule MSB_WAKE_SCHEDULE) if missing.
    Returns True when the job exists after the call. Called from the app
    lifespan when wake_enabled and cron_enabled are both on — the 5-minute
    resident loop exists by default, no manual setup."""
    from msb_v3.cron.store import CronStore

    store = cron_store if cron_store is not None else CronStore()
    try:
        store.get_job("wake-agent")
        return True
    except KeyError:
        pass
    try:
        store.create_job(
            "wake-agent",
            "Wake agent (5-minute resident loop)",
            settings.wake_schedule,
            {"type": "wake_agent", "params": {}},
            governance={"max_retries": 1, "timeout_s": 240.0, "notify_on_failure": True},
        )
        logger.info("seeded wake-agent cron job (%s)", settings.wake_schedule)
        return True
    except ValueError:
        return False
