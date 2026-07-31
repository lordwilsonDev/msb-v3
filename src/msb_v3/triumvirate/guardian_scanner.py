"""Guardian Protocol: scanner, SBOM registry, least privilege, poison pill."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from msb_v3.core.config import settings


_RUNTIME_ROOT = Path(settings.db_path).parent / "triumvirate"
_SBOM_FILE = _RUNTIME_ROOT / "sbom_registry.json"
_LEAST_PRIVILEGE_ROLES: Dict[str, List[str]] = {
    "sub-agent": ["read", "execute"],
    "mesh-peer": ["read"],
    "human-operator": ["*"],
}


@dataclass
class ScanReport:
    risk: str
    findings: List[str]
    blocked: bool = False


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class GuardianScanner:
    def scan_script(self, script: str) -> ScanReport:
        findings: List[str] = []
        blocked_patterns = [
            (r"os\.system\(", "direct shell execution"),
            (r"subprocess\.", "subprocess execution"),
            (r"eval\(", "eval usage"),
            (r"exec\(", "exec usage"),
            (r"__import__\(", "dynamic import"),
            (r"rm\s+-rf\s+/", "dangerous filesystem path"),
        ]
        for pattern, label in blocked_patterns:
            if re.search(pattern, script):
                findings.append(label)
        blocked = len(findings) > 0
        risk = "HIGH" if blocked else "LOW"
        return ScanReport(risk=risk, findings=findings, blocked=blocked)

    def verify_sbom(self, mcp_server_id: str, executable_path: Optional[str] = None) -> bool:
        registry = _load_sbom()
        entry = registry.get(mcp_server_id)
        if entry is None:
            return False
        if executable_path:
            actual = _sha256(Path(executable_path))
            return actual == entry.get("sha256")
        return True

    def enforce_least_privilege(self, agent_token: str, required_scope: str) -> bool:
        role = _role_for_token(agent_token)
        allowed = _LEAST_PRIVILEGE_ROLES.get(role, [])
        if "*" in allowed or required_scope in allowed:
            return True
        return False


class SBOMRegistry:
    def register(self, mcp_server_id: str, executable_path: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        path = Path(executable_path)
        if not path.exists():
            raise FileNotFoundError(executable_path)
        registry = _load_sbom()
        registry[mcp_server_id] = {
            "sha256": _sha256(path),
            "path": str(path),
            "metadata": metadata or {},
        }
        _write_sbom(registry)
        return registry[mcp_server_id]

    def trusted(self, mcp_server_id: str) -> bool:
        return mcp_server_id in _load_sbom()


class PoisonPill:
    def arm(self) -> Dict[str, Any]:
        payload = {
            "armed": True,
            "armed_at": _now_iso(),
            "proof": hashlib.sha256(_now_iso().encode()).hexdigest()[:16],
        }
        (_RUNTIME_ROOT / "poison_pill.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        return payload

    def detonate(self) -> Dict[str, Any]:
        payload = {
            "armed": False,
            "detonated_at": _now_iso(),
            "actions": ["revoke_sub_agent_tokens", "disable_gateway", "pause_missions"],
        }
        (_RUNTIME_ROOT / "poison_pill.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        return payload


def _load_sbom() -> Dict[str, Any]:
    if not _SBOM_FILE.exists():
        return {}
    try:
        return json.loads(_SBOM_FILE.read_text())
    except Exception:
        return {}


def _write_sbom(registry: Dict[str, Any]) -> None:
    _RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    _SBOM_FILE.write_text(json.dumps(registry, ensure_ascii=False, indent=2))


def _role_for_token(agent_token: str) -> str:
    if agent_token.startswith("sub-"):
        return "sub-agent"
    if agent_token.startswith("mesh-"):
        return "mesh-peer"
    return "human-operator"


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
