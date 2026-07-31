"""Mission Anchor — STATUS.json state engine for Triumvirate."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from msb_v3.core.config import settings

_RUNTIME_ROOT = Path(settings.db_path).parent / "triumvirate"
_STATUS_FILE = _RUNTIME_ROOT / "STATUS.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_status() -> Dict[str, Any]:
    return {
        "scope_hash": None,
        "goal": None,
        "parameters": None,
        "current_phase": "idle",
        "iteration_count": 0,
        "budget_spent_usd": 0.0,
        "started_at": None,
        "updated_at": None,
        "circuit_breaker": False,
    }


def _ensure_file() -> None:
    _RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    if not _STATUS_FILE.exists():
        _STATUS_FILE.write_text(json.dumps(_default_status(), ensure_ascii=False, indent=2))


def _load() -> Dict[str, Any]:
    _ensure_file()
    try:
        return json.loads(_STATUS_FILE.read_text())
    except Exception:
        return _default_status()


def _save(payload: Dict[str, Any]) -> None:
    _ensure_file()
    _STATUS_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2))


def _goal_signature(goal: str, parameters: Optional[Dict[str, Any]] = None) -> str:
    raw = json.dumps({"goal": goal, "parameters": parameters or {}}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class MissionAnchor:
    def scope_lock(self, goal: str, parameters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        signature = _goal_signature(goal, parameters)
        status = _load()
        status.update({
            "scope_hash": signature,
            "goal": goal,
            "parameters": parameters,
            "current_phase": "locked",
            "iteration_count": 0,
            "budget_spent_usd": 0.0,
            "started_at": _now_iso(),
            "updated_at": _now_iso(),
            "circuit_breaker": False,
        })
        _save(status)
        return status

    def update(self, phase: str, iteration_count: int = 0, budget_spent_usd: float = 0.0) -> Dict[str, Any]:
        status = _load()
        status.update({
            "current_phase": phase,
            "iteration_count": max(status.get("iteration_count", 0), iteration_count),
            "budget_spent_usd": max(status.get("budget_spent_usd", 0.0), budget_spent_usd),
            "updated_at": _now_iso(),
        })
        _save(status)
        return status

    def verify(self) -> Dict[str, Any]:
        status = _load()
        current_hash = _goal_signature(status.get("goal") or "", status.get("parameters"))
        valid = status.get("scope_hash") == current_hash
        if not valid:
            self.circuit_breaker_trigger()
        return {"valid": valid, "scope_hash": status.get("scope_hash"), "current_hash": current_hash}

    def circuit_breaker_trigger(self) -> Dict[str, Any]:
        status = _load()
        status.update({
            "circuit_breaker": True,
            "current_phase": "paused",
            "updated_at": _now_iso(),
        })
        _save(status)
        return status

    def read(self) -> Dict[str, Any]:
        return _load()
