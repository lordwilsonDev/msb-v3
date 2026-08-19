"""Demo pin — scripts/demo_governed_loop.py proves the governed loop end to end.

Hermetic by construction: canned client + tool outputs (no model, no
network, no vault), and the audit chain is redirected to a scratch file, so
the demo touches no production state. The MoIE policy, Evidence Spine, audit
stream, and grounded verification are all real.

Pins the three demo claims:

  blocked  -> BLOCKED, 0 model calls, DENY receipt (decision-only)
  allowed  -> PASS, ALLOW receipt with grounded checks, hash recomputes
  both     -> receipts in the same audit stream + a valid scratch chain
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from demo_governed_loop import run_demo  # noqa: E402


@pytest.mark.asyncio
async def test_demo_blocks_dangerous_and_allows_safe(tmp_path: Path) -> None:
    out = await run_demo(tmp_path)

    # --- the dangerous run: denied before any model call ---
    blocked = out["blocked"]
    assert blocked.verdict == "BLOCKED"
    assert blocked.model_calls == 0

    # --- the safe run: allowed with grounded verification ---
    allowed = out["allowed"]
    assert allowed.verdict == "PASS"

    b = out["blocked_receipt"]
    a = out["allowed_receipt"]
    assert b is not None and a is not None

    # blocked receipt: DENY, decision-only evidence language, zero calls
    assert b["moie_verdict"] == "BLOCK"
    assert b["authorization_decision"] == "DENY"
    assert b["model_calls"] == 0
    assert b["verification"]["basis"] == "decision-only"
    assert b["verification"]["grounded_checks"] == []
    assert "verified=decision-only" in b["reconstruction"]

    # allowed receipt: ALLOW, rerun evidence language, grounded checks
    assert a["moie_verdict"] == "APPROVE"
    assert a["authorization_decision"] == "ALLOW"
    assert set(a["capability_granted"]) == {"read_vault", "write_file"}
    assert a["verification"]["basis"] == "rerun"
    assert a["verification"]["hash_recomputed"] is True
    checks = {c["check"] for c in a["verification"]["grounded_checks"]}
    assert checks == {"search_returned_hits", "synthesis_nonempty", "file_written_with_heading"}
    assert "verified=rerun" in a["reconstruction"]
    assert a["verification_result"] == allowed.deterministic_hash

    # both receipts in the same stream, spine-linked audit hashes present
    assert len(out["receipts"]) == 2
    assert {r["request_id"] for r in out["receipts"]} == {blocked.run_id, allowed.run_id}
    assert a["audit_hash"] and b["audit_hash"]
    assert a["timestamps"]["decision"] and a["timestamps"]["execution"] and a["timestamps"]["verification"]

    # the scratch audit chain verifies (every task + trace event landed)
    assert out["chain_verify"]["valid"] is True
    assert out["chain_verify"]["record_count"] > 0

    # the receipt file is human-inspectable JSONL
    lines = out["audit_log"].read_text().splitlines()
    assert len(lines) == 2
    assert all(json.loads(ln)["request_id"] for ln in lines)
