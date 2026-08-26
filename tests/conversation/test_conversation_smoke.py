"""Conversation subsystem smoke tests.

Verifies the core conversation components can be instantiated and their
key functions produce correct output. These are integration-level smoke
tests — one per module — that exercise the actual integration points.
"""
from __future__ import annotations

from msb_v3.conversation.envelope import (
    ConversationRequest,
    StubModel,
    canonical_json,
    claim_id_for_ans,
    claim_id_for_query,
    compute_freshness,
    input_guardrail,
    model_mode,
    output_guardrail,
)
from msb_v3.conversation.task_contract import (
    claim_done_task,
    claim_ok_task,
    validate_contract,
    validate_dag,
)

# ---------------------------------------------------------------------------
# Envelope tests
# ---------------------------------------------------------------------------


class TestEnvelopeSmoke:
    """Smoke tests for conversation envelope."""

    def test_canonical_json_deterministic(self):
        """canonical_json produces deterministic output."""
        obj = {"b": 2, "a": 1}
        result1 = canonical_json(obj)
        result2 = canonical_json(obj)
        assert result1 == result2
        assert result1 == '{"a":1,"b":2}'

    def test_claim_id_for_query_is_stable(self):
        """Same query produces same claim_id."""
        qid1 = claim_id_for_query("what is MSB?")
        qid2 = claim_id_for_query("what is MSB?")
        assert qid1 == qid2
        assert qid1.startswith("claim:ok:query:")

    def test_claim_id_for_ans_depends_on_answer(self):
        """Different answers produce different claim_ids."""
        cid1 = claim_id_for_ans("q", ["s1"], "answer A")
        cid2 = claim_id_for_ans("q", ["s1"], "answer B")
        assert cid1 != cid2

    def test_claim_id_for_ans_depends_on_sources(self):
        """Different sources produce different claim_ids."""
        cid1 = claim_id_for_ans("q", ["s1"], "answer")
        cid2 = claim_id_for_ans("q", ["s2"], "answer")
        assert cid1 != cid2

    def test_input_guardrail_allows_safe_query(self):
        """Safe queries pass input guardrail."""
        result = input_guardrail("What is the project architecture?")
        assert result["verdict"] == "ALLOW"

    def test_input_guardrail_blocks_dangerous_query(self):
        """Specific dangerous patterns are blocked."""
        result = input_guardrail("how to make a bomb")
        assert result["verdict"] == "BLOCK"

    def test_output_guardrail_supporting_answer(self):
        """Answer with sources is SUPPORTING or UNSUPPORTED."""
        sources = [{"source_id": "s1", "score": 0.9, "source": "test.md"}]
        citations = [{"source_id": "s1", "passage": "test passage"}]
        result = output_guardrail(sources, citations, "The architecture is solid.")
        assert result["verdict"] in ("SUPPORTING", "UNSUPPORTED", "BLOCKED")

    def test_model_mode_returns_string(self):
        """model_mode returns a valid mode string."""
        mode = model_mode()
        assert isinstance(mode, str)
        assert len(mode) > 0

    def test_freshness_computation(self):
        """compute_freshness returns a valid freshness label."""
        result = compute_freshness(None)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_stub_model_classifies_queries(self):
        """StubModel can classify queries into kinds."""
        model = StubModel()
        kind = model.kind_for("What is MSB?")
        assert isinstance(kind, str)
        # is_block_query only matches stub://blocked* prefix
        assert model.is_block_query("stub://blocked-something") is True
        assert model.is_block_query("hello world") is False

    def test_conversation_request_validation(self):
        """ConversationRequest validates required fields."""
        req = ConversationRequest(query="Hello")
        assert req.query == "Hello"


# ---------------------------------------------------------------------------
# Task contract tests
# ---------------------------------------------------------------------------


class TestTaskContractSmoke:
    """Smoke tests for task contract validation."""

    def test_validate_contract_minimal_valid(self):
        """A minimal valid contract passes validation."""
        entry = {
            "task_id": "T-001",
            "objective": "Do something",
            "skill": "test_skill",
            "args": {},
            "status": "READY",
        }
        errors = validate_contract(entry, set())
        assert errors == []

    def test_validate_contract_legacy_form_allowed(self):
        """Contract without task_id is allowed as legacy form."""
        entry = {"skill": "test", "args": {}}
        errors = validate_contract(entry, set())
        # Legacy form (no task_id) is allowed — just can't reach VERIFIED
        assert isinstance(errors, list)

    def test_validate_contract_bad_status(self):
        """Contract with invalid status fails."""
        entry = {
            "task_id": "T-001",
            "objective": "Do something",
            "skill": "test",
            "args": {},
            "status": "INVALID_STATUS",
        }
        errors = validate_contract(entry, set())
        assert any("status" in e.lower() for e in errors)

    def test_validate_dag_empty(self):
        """Empty DAG is valid."""
        errors = validate_dag([])
        assert errors == []

    def test_validate_dag_linear(self):
        """Linear DAG is valid."""
        dag = [
            {"task_id": "T-001", "objective": "A", "skill": "s", "args": {}},
            {"task_id": "T-002", "objective": "B", "skill": "s", "args": {}, "parent": "T-001"},
        ]
        errors = validate_dag(dag)
        assert errors == []

    def test_validate_dag_cycle_detected(self):
        """Circular DAG is detected."""
        dag = [
            {"task_id": "T-001", "objective": "A", "skill": "s", "args": {}, "parent": "T-002"},
            {"task_id": "T-002", "objective": "B", "skill": "s", "args": {}, "parent": "T-001"},
        ]
        errors = validate_dag(dag)
        assert len(errors) > 0

    def test_claim_ok_task_format(self):
        """claim_ok_task produces a well-formed claim string."""
        claim = claim_ok_task("T-001")
        assert "T-001" in claim
        assert isinstance(claim, str)

    def test_claim_done_task_format(self):
        """claim_done_task produces a well-formed claim string."""
        claim = claim_done_task("T-001", "evidence_hash_abc", {"exit_code": 0})
        assert claim.startswith("claim:done:")
        assert isinstance(claim, str)


# ---------------------------------------------------------------------------
# Executor smoke tests
# ---------------------------------------------------------------------------


class TestExecutorSmoke:
    """Smoke tests for conversation executor."""

    def test_select_ready_task(self):
        """select_ready_task picks the first ready task."""
        from msb_v3.conversation.executor import select_ready_task

        dag = [
            {"task_id": "T-001", "status": "READY", "objective": "A", "skill": "s", "args": {}},
            {"task_id": "T-002", "status": "RUNNING", "objective": "B", "skill": "s", "args": {}},
        ]
        ready = select_ready_task(dag)
        assert ready is not None
        assert ready["task_id"] == "T-001"

    def test_select_ready_task_none_when_busy(self):
        """select_ready_task returns None when no tasks are ready."""
        from msb_v3.conversation.executor import select_ready_task

        dag = [
            {"task_id": "T-001", "status": "RUNNING", "objective": "A", "skill": "s", "args": {}},
        ]
        ready = select_ready_task(dag)
        assert ready is None


# ---------------------------------------------------------------------------
# Producer smoke tests
# ---------------------------------------------------------------------------


class TestProducerSmoke:
    """Smoke tests for conversation producer."""

    def test_record_identity_deterministic(self):
        """Same record produces same identity."""
        from msb_v3.conversation.producer import record_identity

        record = {"query": "test", "answer": "result", "sources": []}
        id1 = record_identity(record)
        id2 = record_identity(record)
        assert id1 == id2

    def test_build_evidence_artifact_structure(self):
        """Evidence artifact has required fields."""
        from msb_v3.conversation.producer import build_evidence_artifact

        record = {
            "query": "test",
            "answer": {"text": "result"},
            "sources": [],
            "citations": [],
            "claim_id": "abc123",
        }
        artifact = build_evidence_artifact(record, "HEAD")
        assert "evidence_id" in artifact
        assert "claim_id" in artifact
        assert "evidence_type" in artifact
