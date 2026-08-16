"""Vesta service composition (completion blueprint Phase 1.4).

Builds the Vesta trust/evidence perimeter's services in one place instead of
module-level singletons in ``vesta/api.py``. The router resolves them through
the ``ApplicationContainer`` (``get_container_dep``), so tests substitute a
tmp-backed ``VestaServices`` without monkeypatching module globals.

Kept separate from ``vesta/api.py`` (the router) so the composition root can
import it without a router -> container -> services import cycle: the router
imports the container, the container imports this module, and this module only
imports the low-level Vesta/node/governance services (never the router).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from msb_v3.core.config import settings
from msb_v3.evidence.spine import DecisionEvidenceStore
from msb_v3.governance.killswitch import KillSwitch
from msb_v3.node.filesystem import FileReader, FileWriter
from msb_v3.node.identity import IdentityStore
from msb_v3.uac.audit_chain import AuditChainLike
from msb_v3.uac.chain_anchor import anchored_chain_from_env
from msb_v3.vesta.adapter import VestaMSBAdapter
from msb_v3.vesta.approvals import VestaApprovalStore
from msb_v3.vesta.evidence import EvidenceStore
from msb_v3.vesta.read import VestaReadService
from msb_v3.vesta.runtime import VestaTaskStore
from msb_v3.vesta.shell import ShellExecutor, VestaShellApprovalStore, VestaShellService
from msb_v3.vesta.write import VestaWriteService


@dataclass
class VestaServices:
    """The Vesta perimeter's services, all explicit and non-optional.

    Construct via ``build_vesta_services()`` (which wires every field) or, in
    tests, directly with tmp-backed services. The audit chain may be a plain
    ``AuditChain`` or an ``AnchoredAuditChain`` (``AuditChainLike``).
    """

    audit: AuditChainLike
    tasks: VestaTaskStore
    evidence: EvidenceStore
    spine: DecisionEvidenceStore
    adapter: VestaMSBAdapter
    write_approvals: VestaApprovalStore
    shell_approvals: VestaShellApprovalStore
    signed_identity: IdentityStore
    read_service: VestaReadService
    shell_service: VestaShellService
    write_service: VestaWriteService


def _resolve_node_paths() -> tuple[Path, Path]:
    """The Vesta sandbox root + node DB, made absolute against msb_home."""
    node_root = Path(settings.node_sandbox_root)
    if not node_root.is_absolute():
        node_root = Path(settings.msb_home) / node_root
    node_db = Path(settings.node_db_path)
    if not node_db.is_absolute():
        node_db = Path(settings.msb_home) / node_db
    return node_root, node_db


def build_vesta_services(**overrides: Any) -> VestaServices:
    """Composition root for the Vesta perimeter; every field is overridable.

    Anchored when ``MSB_CHAIN_ANCHOR_KEY`` is configured (T7 fix: the
    write-path chain re-anchors an external signed tip snapshot after every
    append, so a whole-audit-DB replacement is detectable); plain AuditChain
    otherwise.
    """
    node_root, node_db = _resolve_node_paths()

    audit = overrides.pop("audit", None) or anchored_chain_from_env()
    tasks = overrides.pop("tasks", None) or VestaTaskStore()
    evidence = overrides.pop("evidence", None) or EvidenceStore()
    spine = overrides.pop("spine", None) or DecisionEvidenceStore()
    write_approvals = overrides.pop("write_approvals", None) or VestaApprovalStore()
    shell_approvals = overrides.pop("shell_approvals", None) or VestaShellApprovalStore()

    signed_identity = overrides.pop("signed_identity", None) or IdentityStore(
        str(node_db),
        settings.node_pairing_code,
        session_ttl_s=settings.node_session_ttl_s,
        clock_skew_s=settings.node_clock_skew_s,
    )
    read_service = overrides.pop("read_service", None) or VestaReadService(
        audit,
        tasks,
        evidence,
        FileReader(node_root, settings.node_max_read_bytes),
        KillSwitch(str(node_db), audit_chain=audit),
    )
    shell_service = overrides.pop("shell_service", None) or VestaShellService(
        audit,
        tasks,
        evidence,
        shell_approvals,
        ShellExecutor(
            node_root,
            timeout_s=settings.vesta_shell_timeout_s,
            max_output_bytes=settings.vesta_shell_max_output_bytes,
        ),
        KillSwitch(str(node_db), audit_chain=audit),
    )
    write_service = overrides.pop("write_service", None) or VestaWriteService(
        audit,
        tasks,
        evidence,
        write_approvals,
        FileWriter(node_root, settings.node_max_read_bytes),
        KillSwitch(str(node_db), audit_chain=audit),
    )
    adapter = overrides.pop("adapter", None) or VestaMSBAdapter(
        audit, tasks, evidence, spine=spine
    )

    if overrides:
        raise TypeError(f"unknown vesta service override(s): {sorted(overrides)}")

    return VestaServices(
        audit=audit,
        tasks=tasks,
        evidence=evidence,
        spine=spine,
        adapter=adapter,
        write_approvals=write_approvals,
        shell_approvals=shell_approvals,
        signed_identity=signed_identity,
        read_service=read_service,
        shell_service=shell_service,
        write_service=write_service,
    )
