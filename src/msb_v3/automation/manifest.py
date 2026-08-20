"""Durable automation manifest — every automation the brain created (or
would have created), append-only JSONL under data/runtime/automation/.

The manifest is the ledger the operator reads (GET /automation/manifest):
provider, name, description, status (created / dry_run / blocked / failed),
the webhook/API detail, and any recorded cost. JSONL append-only mirrors the
audit-stream convention — no rewrites, the file is the record.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from msb_v3.core.config import settings

logger = logging.getLogger(__name__)


def default_manifest_path() -> Path:
    if settings.automation_manifest_path:
        return Path(settings.automation_manifest_path)
    return Path(settings.db_path).parent / "runtime" / "automation" / "manifest.jsonl"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Manifest:
    """Append-only ledger of automation creation attempts."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = Path(path) if path else default_manifest_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(
        self,
        *,
        provider: str,
        name: str,
        description: str,
        status: str,
        summary: str,
        detail: Optional[Dict[str, Any]] = None,
        budget_usd: float = 0.0,
        schedule: Optional[str] = None,
        action: Optional[Dict[str, Any]] = None,
        enabled: bool = True,
    ) -> Dict[str, Any]:
        """Append a creation/attempt record. ``schedule`` + ``action`` make
        the entry a *living* automation the dispatcher can execute (Stage 1:
        the manifest IS the automation — append to create, flip enabled to
        disable); provider/name/description/status remain the immutable
        ledger record."""
        entry: Dict[str, Any] = {
            "id": f"auto-{uuid.uuid4().hex[:12]}",
            "ts": _now(),
            "provider": provider,
            "name": name,
            "description": description,
            "status": status,
            "summary": summary,
            "detail": detail or {},
            "budget_usd": round(float(budget_usd), 4),
        }
        if schedule:
            entry["schedule"] = schedule
        if action:
            entry["action"] = action
        if not enabled:
            entry["enabled"] = False
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        return entry

    def list(self, limit: int = 50) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: List[Dict[str, Any]] = []
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        rows.sort(key=lambda r: r.get("ts", ""), reverse=True)
        return rows[: max(0, int(limit))]

    def get(self, automation_id: str) -> Dict[str, Any]:
        """Fetch one entry by id (newest matching record wins)."""
        for row in self.list(limit=10_000):
            if row.get("id") == automation_id:
                return row
        raise KeyError(f"unknown automation: {automation_id}")
