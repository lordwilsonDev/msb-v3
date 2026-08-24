"""Evidence ingestion — audit.jsonl, ops reports, health checks.

Reads live evidence artifacts: audit chain length, health status, ops audit
reports. Gracefully degrades when the server isn't running — marks UNKNOWN.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from msb_v3.plei.provenance import Provenance, Provenanced


@dataclass(slots=True)
class EvidenceFacts:
    """Evidence-derived facts."""

    audit_chain_entries: Provenanced = field(default_factory=Provenanced.unknown)
    audit_recent: Provenanced = field(default_factory=Provenanced.unknown)
    live_health: Provenanced = field(default_factory=Provenanced.unknown)
    ops_audits: Provenanced = field(default_factory=Provenanced.unknown)
    msb_version: Provenanced = field(default_factory=Provenanced.unknown)


def ingest_evidence(project_root: str | Path) -> EvidenceFacts:
    """Ingest evidence artifacts from the project.

    Also probes the live server at localhost:8766 if reachable.
    """
    root = Path(project_root).resolve()
    facts = EvidenceFacts()

    # Audit chain
    audit_log = root / "logs" / "audit.jsonl"
    if audit_log.is_file():
        try:
            lines = audit_log.read_text().strip().split("\n")
            facts.audit_chain_entries = Provenanced.observed(
                len(lines), str(audit_log.relative_to(root))
            )
            # Last 3 entries
            recent: list[dict[str, Any]] = []
            for line in lines[-3:]:
                try:
                    recent.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
            facts.audit_recent = Provenanced.observed(recent, str(audit_log.relative_to(root)))
        except Exception:
            pass

    # Ops audits
    audit_dir = root / "audit"
    if audit_dir.is_dir():
        reports = sorted(audit_dir.glob("*_audit.md"))
        facts.ops_audits = Provenanced.observed(
            [r.name for r in reports], f"{audit_dir.relative_to(root)}"
        )

    # Live server health probe
    try:
        import urllib.request
        req = urllib.request.Request("http://127.0.0.1:8766/health")
        with urllib.request.urlopen(req, timeout=3) as resp:
            body = json.loads(resp.read())
            facts.live_health = Provenanced.observed(body, "GET :8766/health")
            if isinstance(body.get("version"), str):
                facts.msb_version = Provenanced.observed(body["version"], "GET :8766/health")
    except Exception:
        facts.live_health = Provenanced(value=None, provenance=Provenance.UNKNOWN,
                                         source="server unreachable")

    return facts


def evidence_facts_as_dict(facts: EvidenceFacts) -> dict[str, Any]:
    return {k: getattr(facts, k).as_dict() for k in (
        "audit_chain_entries", "audit_recent", "live_health",
        "ops_audits", "msb_version")}