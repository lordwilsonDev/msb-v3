from __future__ import annotations

from pathlib import Path

from msb_v3.governance.killswitch import KillSwitch
from msb_v3.node.filesystem import FileReader
from msb_v3.uac.audit_chain import AuditChain
from msb_v3.vesta.evidence import EvidenceStore
from msb_v3.vesta.models import VestaFileReadRequest
from msb_v3.vesta.read import VestaReadService
from msb_v3.vesta.runtime import VestaTaskStore


def make_service(tmp_path: Path) -> VestaReadService:
    audit = AuditChain(str(tmp_path / "audit.db"))
    tasks = VestaTaskStore(str(tmp_path / "tasks.db"))
    evidence = EvidenceStore(str(tmp_path / "evidence"), str(tmp_path / "evidence.db"))
    root = tmp_path / "sandbox"
    root.mkdir()
    return VestaReadService(
        audit,
        tasks,
        evidence,
        FileReader(root, max_bytes=100),
        KillSwitch(str(tmp_path / "kill.db"), audit_chain=audit),
    )


def test_read_completes_with_content_evidence_and_verification(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    (tmp_path / "sandbox" / "hello.txt").write_text("hello")

    result = service.execute(VestaFileReadRequest(path="hello.txt"))

    assert result["status"] == "completed"
    assert result["result"]["content"] == "hello"
    assert result["verification"]["ok"] is True
    assert len(result["evidence_refs"]) == 4
    task = service.tasks.get(result["task_id"])
    assert task["state"] == "COMPLETED"
    assert task["actor"] == "operator"
    assert all(service.evidence.get(ref)["verified"] for ref in result["evidence_refs"])
    assert service.audit.verify_chain()["valid"] is True


def test_read_rejects_scope_escape_and_quarantines_missing_target(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")

    escaped = service.execute(VestaFileReadRequest(path="../outside.txt"))
    assert escaped["status"] == "quarantined"
    assert service.tasks.get(escaped["task_id"])["state"] == "QUARANTINED"

    missing = service.execute(VestaFileReadRequest(path="missing.txt"))
    assert missing["status"] == "quarantined"
    assert "does not exist" in str(missing["error"])


def test_read_respects_size_limit_and_kill_switch(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    (tmp_path / "sandbox" / "large.txt").write_text("x" * 101)
    oversized = service.execute(VestaFileReadRequest(path="large.txt"))
    assert oversized["status"] == "quarantined"

    service.kill_switch.arm("test", "read safety test")
    blocked = service.execute(VestaFileReadRequest(path="large.txt"))
    assert blocked["status"] == "quarantined"
    assert blocked["error"] == "kill switch armed"
    assert service.tasks.get(blocked["task_id"])["state"] == "QUARANTINED"
