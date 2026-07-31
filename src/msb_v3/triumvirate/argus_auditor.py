"""Argus Auditor — self-annealing audit engine + mulch learnings store."""
from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from msb_v3.core.config import settings

_RUNTIME_ROOT = Path(settings.db_path).parent / "triumvirate"
_MULCH_DB = _RUNTIME_ROOT / "mulch_learnings.db"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _init_db() -> None:
    _RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(_MULCH_DB) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mulch_learnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                component TEXT NOT NULL,
                finding_type TEXT NOT NULL,
                description TEXT NOT NULL,
                resolution_status TEXT NOT NULL DEFAULT 'open'
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mulch_timestamp ON mulch_learnings(timestamp)"
        )


@dataclass
class MulchFinding:
    component: str
    finding_type: str
    description: str
    resolution_status: str = "open"


class ArgusAuditor:
    def __init__(self) -> None:
        _init_db()

    def record_mulch(self, finding: MulchFinding) -> Dict[str, Any]:
        ts = datetime.now(timezone.utc).timestamp()
        with sqlite3.connect(_MULCH_DB) as conn:
            cur = conn.execute(
                "INSERT INTO mulch_learnings(timestamp, component, finding_type, description, resolution_status) VALUES (?,?,?,?,?)",
                (ts, finding.component, finding.finding_type, finding.description, finding.resolution_status),
            )
            return {"id": cur.lastrowid, "timestamp": ts, **finding.__dict__}

    def audit_directives(self, directives_dir: Optional[str] = None) -> List[Dict[str, Any]]:
        findings: List[Dict[str, Any]] = []
        target = Path(directives_dir) if directives_dir else Path(settings.db_path).parent.parent / "directives"
        if not target.exists():
            return findings
        for path in target.glob("*.md"):
            content = path.read_text(errors="ignore")
            if "FIXME" in content or "TODO" in content:
                findings.append(self.record_mulch(MulchFinding(
                    component="directives",
                    finding_type="outdated_rule",
                    description=f"{path.name} contains unresolved FIXME/TODO",
                )))
        return findings

    def audit_memory(self, memory_file: Optional[str] = None) -> List[Dict[str, Any]]:
        findings: List[Dict[str, Any]] = []
        target = Path(memory_file) if memory_file else Path(settings.db_path).parent.parent / "memory.mmd"
        if not target.exists():
            return findings
        content = target.read_text(errors="ignore")
        if "orphan" in content.lower():
            findings.append(self.record_mulch(MulchFinding(
                component="memory",
                finding_type="orphan_node",
                description="memory.mmd may contain orphan nodes",
            )))
        return findings

    def audit_soul(self, soul_file: Optional[str] = None) -> List[Dict[str, Any]]:
        findings: List[Dict[str, Any]] = []
        target = Path(soul_file) if soul_file else Path(settings.db_path).parent.parent / "soul.md"
        if not target.exists():
            return findings
        content = target.read_text(errors="ignore")
        if "drift" in content.lower():
            findings.append(self.record_mulch(MulchFinding(
                component="soul",
                finding_type="drift",
                description="soul.md contains drift markers",
            )))
        return findings

    def audit_run_logs(self, logs_dir: Optional[str] = None) -> List[Dict[str, Any]]:
        findings: List[Dict[str, Any]] = []
        target = Path(logs_dir) if logs_dir else Path(settings.db_path).parent / "logs"
        if not target.exists():
            return findings
        for path in target.glob("*.log"):
            text = path.read_text(errors="ignore")
            if "ERROR" in text:
                findings.append(self.record_mulch(MulchFinding(
                    component="run_logs",
                    finding_type="error_pattern",
                    description=f"{path.name} contains ERROR entries",
                )))
        return findings

    def run(
        self,
        directives_dir: Optional[str] = None,
        memory_file: Optional[str] = None,
        soul_file: Optional[str] = None,
        logs_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        started = _now_iso()
        findings = []
        findings.extend(self.audit_directives(directives_dir))
        findings.extend(self.audit_memory(memory_file))
        findings.extend(self.audit_soul(soul_file))
        findings.extend(self.audit_run_logs(logs_dir))
        return {
            "started_at": started,
            "finished_at": _now_iso(),
            "findings": findings,
            "count": len(findings),
        }
