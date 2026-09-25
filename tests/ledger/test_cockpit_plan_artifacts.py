from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "config" / "cockpit_verification_manifest.yaml"
MATRIX = ROOT / "docs" / "audits" / "msb-cockpit-verification-matrix-v1.md"
PLAN = ROOT / "docs" / "audits" / "msb-cockpit-build-plan-v1-2026-09-24.md"
SCHEMA = ROOT / "docs" / "audits" / "msb-cockpit-audit-schema-v1.md"
VECTORS = ROOT / "tests" / "fixtures" / "audit_golden_vectors.json"


def test_manifest_contains_exactly_104_unique_gates() -> None:
    data = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    gates = data["gates"]
    ids = [gate["id"] for gate in gates]
    assert len(gates) == 104
    assert ids == list(range(1, 105))
    assert all(gate["status"] in data["status_values"] for gate in gates)
    assert all(gate["priority"] in data["priority_values"] for gate in gates)


def test_every_gate_has_an_enforcement_and_test_contract() -> None:
    data = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    for gate in data["gates"]:
        assert gate["requirement"]
        assert gate["enforcement"]
        assert gate["test"]
        assert isinstance(gate["physical"], bool)
        assert isinstance(gate["evidence"], list)


def test_schema_fixture_marks_pending_digests_honestly() -> None:
    data = json.loads(VECTORS.read_text(encoding="utf-8"))
    assert data["status"] == "implemented_pending_producer_integration"
    assert data["vectors"]
    for vector in data["vectors"]:
        assert vector["expected_record_hash"]
        assert vector["must_fail_mutations"]


def test_plan_and_matrix_keep_physical_and_independence_claims_explicit() -> None:
    plan = PLAN.read_text(encoding="utf-8")
    matrix = MATRIX.read_text(encoding="utf-8")
    schema = SCHEMA.read_text(encoding="utf-8")
    for text in (plan, matrix):
        assert "not a claim" in text.lower() or "not proven" in text.lower()
        assert "independent" in text.lower()
    assert "physical" in schema.lower()
    assert "NaN" in schema
    assert "prev_hash" in schema
    assert "record_hash" in schema
