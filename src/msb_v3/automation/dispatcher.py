"""The dispatcher — executes due *living* automations.

Ticked by the wake cycle (one clock, all automations): reads the
AutomationState for enabled entries whose next_run has arrived, pulls the
action spec from the manifest (the immutable ledger), executes it, and
records the run. The manifest IS the automation; this is the muscle.

Actions (fail-closed):

- ``webhook_post`` — POST ``url`` with an optional static ``payload``.
  The URL's host must be on the allowlist (MSB_AUTOMATION_WEBHOOK_HOSTS,
  default: loopback + the configured providers' own hosts — n8n, Make
  webhook, GHL). A host outside the allowlist is refused, never called.
- Unknown action types are refused (a misconfigured entry is a FAILED
  run, never a silent no-op).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from msb_v3.automation.manifest import Manifest
from msb_v3.automation.state import AutomationState
from msb_v3.core.config import settings

logger = logging.getLogger(__name__)


def default_allowed_hosts() -> List[str]:
    """Loopback + the configured providers' own hosts — the automations we
    build work out of the box, arbitrary internet hosts stay closed until
    explicitly added via MSB_AUTOMATION_WEBHOOK_HOSTS."""
    hosts: List[str] = ["127.0.0.1", "localhost", "::1"]
    for url in (settings.n8n_base_url, settings.make_webhook_url, settings.ghl_base_url):
        try:
            host = urlparse(url).hostname
            if host:
                hosts.append(host)
        except ValueError:
            continue
    for extra in settings.automation_webhook_hosts.split(","):
        extra = extra.strip().lower()
        if extra:
            hosts.append(extra)
    return list(dict.fromkeys(hosts))


def _host_allowed(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    return (parsed.hostname or "").lower() in set(default_allowed_hosts())


def run_due(
    *,
    state: Optional[AutomationState] = None,
    manifest: Optional[Manifest] = None,
    now: Optional[datetime] = None,
    transport: Any = None,
) -> Dict[str, Any]:
    """Execute every due automation. Returns the action-result shape the
    wake cycle folds into its summary. Hermetic: ``state``/``manifest``/
    ``transport`` injectable for tests."""
    state = state if state is not None else AutomationState()
    manifest = manifest if manifest is not None else Manifest()
    due = state.due(now=now)
    if not due:
        return {"ok": True, "summary": "dispatcher: nothing due", "detail": {"ran": [], "failed": []}}

    ran: List[str] = []
    failed: List[Dict[str, str]] = []
    for item in due:
        auto_id = item["automation_id"]
        entry: Optional[Dict[str, Any]] = None
        try:
            entry = manifest.get(auto_id)
        except KeyError:
            state.mark_run(auto_id, "FAILED", f"manifest entry missing: {auto_id}")
            failed.append({"id": auto_id, "error": "manifest entry missing"})
            continue
        action = entry.get("action") if isinstance(entry, dict) else None
        if not isinstance(action, dict):
            state.mark_run(auto_id, "FAILED", "no action spec on manifest entry")
            failed.append({"id": auto_id, "error": "no action spec"})
            continue
        try:
            _execute_action(action, transport=transport)
            state.mark_run(auto_id, "SUCCESS", _action_summary(action))
            ran.append(auto_id)
        except Exception as exc:  # noqa: BLE001 — one failure never aborts the tick
            state.mark_run(auto_id, "FAILED", f"{type(exc).__name__}: {exc}")
            failed.append({"id": auto_id, "error": f"{type(exc).__name__}: {exc}"})
            logger.warning("dispatcher: automation %s failed: %s", auto_id, exc)
    return {
        "ok": True,
        "summary": f"dispatcher: {len(ran)} ran, {len(failed)} failed",
        "detail": {"ran": ran, "failed": failed},
    }


def _execute_action(action: Dict[str, Any], *, transport: Any = None) -> None:
    """Execute one action spec; raises on any failure (fail-closed)."""
    action_type = str(action.get("type", ""))
    if action_type == "webhook_post":
        url = str(action.get("url", ""))
        if not url:
            raise ValueError("webhook_post requires a url")
        if not _host_allowed(url):
            raise ValueError(f"webhook_post host not on allowlist (MSB_AUTOMATION_WEBHOOK_HOSTS): {url}")
        payload = action.get("payload")
        if payload is None:
            payload = {}
        if isinstance(payload, dict) and "$now" in payload:
            payload = {**payload, "$now": datetime.now(timezone.utc).isoformat()}
        timeout_s = float(action.get("timeout_s", 10.0))
        with httpx.Client(timeout=timeout_s, transport=transport) as client:
            resp = client.post(url, json=payload if isinstance(payload, dict) else payload)
            if resp.status_code >= 400:
                raise RuntimeError(f"webhook_post HTTP {resp.status_code}: {resp.text[:200]}")
        return
    raise ValueError(f"unknown automation action type: {action_type!r}")


def _action_summary(action: Dict[str, Any]) -> str:
    atype = action.get("type", "?")
    if atype == "webhook_post":
        return f"webhook_post {action.get('url', '')}"
    return atype
