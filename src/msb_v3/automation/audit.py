"""Stage 4 — self-maintenance: the wake cycle audits its own manifest.

Every cycle the brain checks the system it runs on: provider seams
configured or blocked (and why), the LLM budget vs its cap, living
automations whose last run FAILED (dead hooks), and state rows whose
manifest entry vanished (drift). Notable findings land in the wake outbox
(source=audit) so the operator sees them — but only when the picture
*changed*, so a healthy system stays silent and a broken one keeps talking
until it is fixed. No LLM, no spend: the audit is deterministic.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from msb_v3.automation.budget import BudgetLedger
from msb_v3.automation.clients import PROVIDERS, get_client
from msb_v3.automation.manifest import Manifest
from msb_v3.automation.state import AutomationState
from msb_v3.core.config import settings
from msb_v3.wake.store import WakeStore

logger = logging.getLogger(__name__)

_BUDGET_WARN_FRACTION = 0.8


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_audit_state_path() -> Path:
    if settings.automation_manifest_path:
        base = Path(settings.automation_manifest_path).parent
    else:
        base = Path(settings.db_path).parent / "runtime" / "automation"
    return base / "audit.json"


def run_audit(
    *,
    manifest: Optional[Manifest] = None,
    state: Optional[AutomationState] = None,
    budget: Optional[BudgetLedger] = None,
    wake: Optional[WakeStore] = None,
    audit_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """One self-maintenance pass. Returns ``{ok, findings, changed, summary}``.
    When the findings picture changed, a summary lands in the wake outbox
    (source=audit) — never the inbox, so the agent does not talk to itself
    and the audit costs nothing."""
    manifest = manifest if manifest is not None else Manifest()
    state = state if state is not None else AutomationState()
    budget = budget if budget is not None else BudgetLedger()
    wake = wake if wake is not None else WakeStore()
    path = audit_path if audit_path is not None else default_audit_state_path()

    findings: List[Dict[str, str]] = []

    # 1. Provider seams — configured or blocked-with-reason.
    for provider in sorted(PROVIDERS):
        client = get_client(provider)
        if not client.available():
            findings.append(
                {
                    "kind": "provider",
                    "subject": provider,
                    "severity": "warn",
                    "detail": client.unavailable_reason(),
                }
            )

    # 2. Budget — spent past the warn fraction of the cap.
    status = budget.status()
    cap = float(status.get("cap_usd", 0) or 0)
    spent = float(status.get("spent_usd", 0) or 0)
    if cap > 0 and spent >= _BUDGET_WARN_FRACTION * cap:
        findings.append(
            {
                "kind": "budget",
                "subject": "llm_brain",
                "severity": "warn",
                "detail": f"brain spend {spent:.4f}/{cap:.4f} USD ({100.0 * spent / cap:.0f}% of cap)",
            }
        )

    # 3. Dead hooks — living automations whose last run failed.
    for row in state.list():
        if row["last_run_status"] == "FAILED":
            findings.append(
                {
                    "kind": "dead_hook",
                    "subject": row["automation_id"],
                    "severity": "warn",
                    "detail": (row["last_run_summary"] or "last run FAILED")[:200],
                }
            )

    # 4. Drift — state rows whose manifest entry vanished (a disabled or
    #    re-registered automation should be cleaned up, not silently dead).
    for row in state.list():
        try:
            manifest.get(row["automation_id"])
        except KeyError:
            findings.append(
                {
                    "kind": "drift",
                    "subject": row["automation_id"],
                    "severity": "error",
                    "detail": "state row has no manifest entry",
                }
            )

    fingerprint = json.dumps(findings, sort_keys=True, default=str)
    changed = True
    if path.exists():
        try:
            prev = json.loads(path.read_text(encoding="utf-8"))
            changed = prev.get("fingerprint") != fingerprint
        except (json.JSONDecodeError, OSError):
            changed = True
    if changed:
        path.write_text(json.dumps({"ts": _now(), "fingerprint": fingerprint}, indent=2), encoding="utf-8")
        if findings:
            lines = [f"[{f['severity']}] {f['kind']} {f['subject']}: {f['detail']}" for f in findings]
            wake.notify("audit findings:\n" + "\n".join(lines), source="audit")
            logger.info("audit: %d finding(s) posted to wake outbox", len(findings))

    summary = f"audit: {len(findings)} finding(s), {'changed' if changed else 'unchanged'}"
    return {"ok": True, "summary": summary, "findings": findings, "changed": changed}
