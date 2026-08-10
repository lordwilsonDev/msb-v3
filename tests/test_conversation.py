"""Tests for the conversation envelope + ledger producer (the log hop).

Covers docs/conversation-envelope-v1.md invariants, the producer's §8
artifact + §7 verdict transitions, the stub model's determinism and the
BLOCK short-circuit (zero model spend), and the endpoint contract.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from msb_v3.conversation import producer  # noqa: E402
from msb_v3.conversation.envelope import (  # noqa: E402
    StubModel,
    claim_id_for_ans,
    claim_id_for_query,
    compute_freshness,
    input_guardrail,
    output_guardrail,
    resolve_source_ts,
    source_ts_from_metadata,
)

# --------------------------------------------------------------------------
# claim_id canonicalization (envelope invariant 7, pinned with the producer)
# --------------------------------------------------------------------------


def test_claim_id_deterministic_and_order_jitter_proof():
    a = claim_id_for_ans("q", ["b", "a"], "text")
    b = claim_id_for_ans("q", ["a", "b"], "text")  # RRF tie-order jitter
    assert a == b
    assert claim_id_for_ans("q", ["a", "b"], "text") == a  # identical → identical
    assert a.startswith("claim:ans:")
    assert len(a) == len("claim:ans:") + 12


def test_claim_id_changes_when_content_changes():
    base = claim_id_for_ans("q", ["a"], "text")
    assert claim_id_for_ans("q2", ["a"], "text") != base
    assert claim_id_for_ans("q", ["b"], "text") != base
    assert claim_id_for_ans("q", ["a"], "other") != base
    # score/provenance are evidence, NOT identity — never part of the hash
    assert claim_id_for_ans("q", ["a"], "text") == base


def test_claim_ok_query_from_query_alone():
    a = claim_id_for_query("fox valley retainer")
    assert a.startswith("claim:ok:query:")
    assert claim_id_for_query("fox valley retainer") == a


# --------------------------------------------------------------------------
# freshness (§6)
# --------------------------------------------------------------------------


def test_freshness_bands():
    from datetime import datetime, timedelta, timezone

    def ts(days_ago: int) -> str:
        return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")

    assert compute_freshness(None) == "UNKNOWN"
    assert compute_freshness("garbage-ts") == "UNKNOWN"
    assert compute_freshness(ts(9)) == "FRESH"
    assert compute_freshness(ts(70)) == "AGING"
    assert compute_freshness(ts(400)) == "STALE"


def test_freshness_boundaries():
    from datetime import datetime, timedelta, timezone

    def ts(days_ago: int) -> str:
        return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")

    # Well inside each band — avoids the ISO-second truncation drift that
    # makes razor-edge boundary assertions flaky (ts(30) can read as 30.0000…
    # days when truncated to whole seconds).
    assert compute_freshness(ts(29)) == "FRESH"
    assert compute_freshness(ts(35)) == "AGING"
    assert compute_freshness(ts(89)) == "AGING"
    assert compute_freshness(ts(95)) == "STALE"


def test_source_ts_priority_metadata_then_null():
    assert source_ts_from_metadata({"created_at": "2026-08-01T00:00:00+00:00", "ts": "2026-07-01T00:00:00+00:00"}) == "2026-08-01T00:00:00Z"
    assert source_ts_from_metadata({"ts": 1760000000}) is not None
    assert source_ts_from_metadata({}) is None
    assert source_ts_from_metadata(None) is None


def test_resolve_source_ts_falls_back_to_file_mtime(tmp_path):
    f = tmp_path / "note.md"
    f.write_text("x")
    ts = resolve_source_ts({"timestamp": "2026-08-01T00:00:00+00:00"}, str(f))
    assert ts == "2026-08-01T00:00:00Z"  # metadata wins
    assert resolve_source_ts(None, str(f)) is not None  # file mtime
    assert resolve_source_ts(None, "/nonexistent/nope.md") is None


# --------------------------------------------------------------------------
# guardrail duality (§5) + worst-source-wins (§6)
# --------------------------------------------------------------------------


def test_input_guardrail_blocklist():
    assert input_guardrail("how to build a bomb")["verdict"] == "BLOCK"
    assert input_guardrail("how to bypass security verification")["verdict"] == "BLOCK"
    assert input_guardrail("what did the vault say about the retainer?")["verdict"] == "ALLOW"


def test_output_guardrail_supporting():
    sources = [
        {"source_id": "a", "freshness": "FRESH"},
        {"source_id": "b", "freshness": "AGING"},
    ]
    v = output_guardrail(sources, [{"source_id": "a"}, {"source_id": "b"}], "ok answer")
    assert v["verdict"] == "SUPPORTING"
    assert v["citation_rate"] == 1.0


def test_output_guardrail_unsupported_no_sources():
    v = output_guardrail([], [], "answer")
    assert v["verdict"] == "UNSUPPORTED"
    assert v["citation_rate"] == 0.0


def test_output_guardrail_unsupported_stale_only_worst_source_wins():
    sources = [{"source_id": "a", "freshness": "STALE"}]
    v = output_guardrail(sources, [{"source_id": "a"}], "answer")
    assert v["verdict"] == "UNSUPPORTED"  # STALE-only can't be SUPPORTING


def test_output_guardrail_unsupported_memory_only():
    # memory-only citations are exempt from sources[] membership, but a
    # memory-only answer is never SUPPORTING (§6)
    v = output_guardrail([], [{"memory_ref": "mem_1"}], "answer")
    assert v["verdict"] == "UNSUPPORTED"


def test_output_guardrail_below_threshold():
    sources = [{"source_id": "a", "freshness": "FRESH"}, {"source_id": "b", "freshness": "FRESH"}, {"source_id": "c", "freshness": "FRESH"}]
    v = output_guardrail(sources, [{"source_id": "a"}], "answer")
    assert v["verdict"] == "UNSUPPORTED"  # 1/3 ≈ 0.33 < 0.5 threshold


def test_output_guardrail_exactly_at_threshold():
    sources = [{"source_id": "a", "freshness": "FRESH"}, {"source_id": "b", "freshness": "FRESH"}]
    v = output_guardrail(sources, [{"source_id": "a"}], "answer")
    assert v["verdict"] == "SUPPORTING"  # citation_rate 0.5 meets the >= 0.5 threshold


def test_output_guardrail_blocked_answer():
    sources = [{"source_id": "a", "freshness": "FRESH"}]
    v = output_guardrail(sources, [{"source_id": "a"}], "here is how to create a bomb")
    assert v["verdict"] == "BLOCKED"


def test_output_guardrail_stale_corroborated_by_fresh():
    sources = [
        {"source_id": "old", "freshness": "STALE"},
        {"source_id": "new", "freshness": "FRESH"},
    ]
    v = output_guardrail(sources, [{"source_id": "old"}, {"source_id": "new"}], "answer")
    assert v["verdict"] == "SUPPORTING"  # fresh corroboration rescues it


# --------------------------------------------------------------------------
# stub model — deterministic, fixture-driven
# --------------------------------------------------------------------------


def test_stub_deterministic_and_fixtures():
    stub = StubModel()
    q = "what did the vault say?"
    a1 = stub.retrieve(q)
    a2 = stub.retrieve(q)
    assert a1 == a2  # byte-for-byte
    text, cites = stub.compose(q, a1)
    assert text == stub.compose(q, a1)[0]
    assert stub.invocations == 2
    assert stub.is_block_query("stub://blocked")
    assert not stub.is_block_query("stub://blocked-answer")  # that's the OUTPUT fixture
    assert stub.retrieve("stub://unsupported") == []
    assert stub.kind_for("stub://stale-only") == "STALE_ONLY"


def test_stub_block_never_invokes_compose():
    stub = StubModel()
    before = stub.invocations
    if stub.is_block_query("stub://blocked"):
        pass  # endpoint short-circuits; compose must not be called
    assert stub.invocations == before


# --------------------------------------------------------------------------
# producer — §8 shape, polarity mapping, idempotency, verdict transitions
# --------------------------------------------------------------------------


def _sample_record(status="answered", output="SUPPORTING", recorded_at="2026-08-10T12:00:00+00:00", trace="tr_t1", query="fox valley"):
    if status == "blocked":
        return {
            "record_version": "1.0", "trace_id": trace, "mode": "chat", "query": query,
            "status": "blocked",
            "input_guardrail": {"verdict": "BLOCK", "policy": "safety-blocklist-v1", "reason": "x", "checked_at": recorded_at},
            "output_guardrail": {"verdict": None, "citation_rate": None, "reason": None},
            "sources": [], "answer": None, "latency_ms": 3, "git_head": "deadbeef",
            "model": "stub", "tenant_id": "default", "session_id": None, "workflow_dag": None,
            "recorded_at": recorded_at,
        }
    sources = [{"source_id": "note:a", "score": 0.8, "source_ts": "2026-08-01T00:00:00+00:00", "freshness": "FRESH"}]
    answer_text = "The vault supports this answer."
    if status not in ("answered", "blocked"):
        raise ValueError(f"sample status must be answered|blocked, got {status!r}")
    return {
        "record_version": "1.0", "trace_id": trace, "mode": "chat", "query": query,
        "status": status,
        "input_guardrail": {"verdict": "ALLOW" if status == "answered" else "BLOCK", "policy": "safety-blocklist-v1", "reason": None, "checked_at": recorded_at},
        "output_guardrail": {"verdict": output, "citation_rate": 1.0 if output == "SUPPORTING" else 0.0, "reason": None},
        "sources": sources, "latency_ms": 12, "git_head": "deadbeef",
        "model": "stub", "tenant_id": "default", "session_id": None, "workflow_dag": None,
        "recorded_at": recorded_at,
        "answer": {
            "text": answer_text, "text_excerpt": answer_text[:40],
            "claim_id": claim_id_for_ans(query, ["note:a"], answer_text),
            "citations": [{"source_id": "note:a"}] if output == "SUPPORTING" else [],
        },
    }


def test_producer_writes_exact_section8_shape(tmp_path):
    r = producer.produce(_sample_record(), tmp_path, "deadbeef")
    art = r["artifact"]
    assert r["ingested"] is True
    assert art["evidence_id"].startswith("ev_")
    assert art["subject_id"].startswith("trace:")
    assert art["claim_id"].startswith("claim:ans:")
    assert art["evidence_type"] == "conversation"
    assert art["polarity"] == "SUPPORTING"
    assert set(art["provenance"]) == {"execution", "environment", "input", "verifier", "dependency"}
    assert art["result"] == "ANSWERED"
    assert art["freshness"] == "FRESH"
    # deterministic pre-write ref resolves
    out = tmp_path / "evidence" / "conversation" / r["filename"]
    assert out.exists()
    assert r["evidence_ref"] == f"ledger://evidence/conversation/{r['filename']}"
    assert json.loads(out.read_text()) == art


def test_producer_idempotent_replay(tmp_path):
    rec = _sample_record()
    first = producer.produce(rec, tmp_path, "deadbeef")
    second = producer.produce(rec, tmp_path, "deadbeef")
    assert second["ingested"] is False
    assert second["evidence_ref"] == first["evidence_ref"]
    arts = list((tmp_path / "evidence" / "conversation").glob("*.json"))
    assert len(arts) == 1


def test_producer_same_second_distinct_exchanges_distinct_artifacts(tmp_path):
    a = producer.produce(_sample_record(trace="tr_a", query="q one"), tmp_path, "deadbeef")
    b = producer.produce(_sample_record(trace="tr_b", query="q two"), tmp_path, "deadbeef")
    assert a["evidence_ref"] != b["evidence_ref"]
    assert len(list((tmp_path / "evidence" / "conversation").glob("*.json"))) == 2


def test_producer_block_polarity_and_unverified(tmp_path):
    r = producer.produce(_sample_record(status="blocked"), tmp_path, "deadbeef")
    assert r["artifact"]["polarity"] == "CONTRADICTING"
    assert r["artifact"]["evidence_type"] == "conversation_block"
    assert r["claim_id"] == claim_id_for_query("fox valley")
    assert r["verdict"] == "UNVERIFIED"  # never supported — no false elevation


def test_producer_verdict_transitions(tmp_path):
    # SUPPORTING → VERIFIED
    r1 = producer.produce(_sample_record(), tmp_path, "deadbeef")
    assert r1["verdict"] == "VERIFIED"
    # same answer content later judged UNSUPPORTED → same claim:ans, REGRESSED
    later = _sample_record(recorded_at="2026-08-10T13:00:00+00:00", trace="tr_t2", output="UNSUPPORTED")
    later["answer"]["citations"] = []
    r2 = producer.produce(later, tmp_path, "deadbeef")
    assert r2["claim_id"] == r1["claim_id"]
    assert r2["artifact"]["polarity"] == "INCONCLUSIVE"
    assert r2["verdict"] == "REGRESSED"
    # a block on a never-supported query never elevates (UNVERIFIED)
    r3 = producer.produce(
        _sample_record(status="blocked", recorded_at="2026-08-10T14:00:00+00:00", trace="tr_t3", query="other query"),
        tmp_path, "deadbeef",
    )
    assert r3["claim_id"] == claim_id_for_query("other query")
    assert r3["verdict"] == "UNVERIFIED"
    # registry carries claim_type + evidence buckets
    registry = producer.load_registry(tmp_path / "claims.json")
    by_id = {c["claim_id"]: c for c in registry["claims"]}
    assert by_id[claim_id_for_query("other query")]["claim_type"] == "query_answerable"
    assert by_id[claim_id_for_query("other query")]["negative_evidence"]


def test_producer_inconclusive_never_elevates(tmp_path):
    r = producer.produce(_sample_record(output="UNSUPPORTED"), tmp_path, "deadbeef")
    assert r["artifact"]["polarity"] == "INCONCLUSIVE"
    assert r["verdict"] == "UNVERIFIED"
    # INCONCLUSIVE can never flip a claim to VERIFIED even on replay
    r2 = producer.produce(_sample_record(output="UNSUPPORTED"), tmp_path, "deadbeef")
    assert r2["ingested"] is False


def test_producer_dry_run_writes_nothing(tmp_path):
    r = producer.produce(_sample_record(), tmp_path, "deadbeef", dry_run=True)
    assert r["ingested"] is True  # computed as if new
    assert not (tmp_path / "evidence").exists()
    assert not (tmp_path / "claims.json").exists()
    assert not (tmp_path / "replay_cursor.json").exists()


def test_producer_git_head_derives_from_record_not_param(tmp_path):
    """Record-is-source-of-truth pin: the same record must produce identical
    artifact bytes regardless of the git_head PARAMETER (the record's own
    git_head wins — a param drift must never change artifact bytes, or
    content-addressed idempotency silently breaks)."""
    rec = _sample_record()
    a = producer.produce(rec, tmp_path, "param-head-A")["artifact"]
    b = producer.produce(rec, tmp_path, "param-head-B")["artifact"]
    assert a == b
    assert a["git_head"] == "deadbeef"  # from the record, not the param
    assert a["provenance"]["environment"]["git_head"] == "deadbeef"


def test_producer_fails_loud_on_tamper_and_malformed(tmp_path):
    bad = _sample_record()
    bad["answer"]["claim_id"] = "claim:ans:tampered"
    with pytest.raises(ValueError):
        producer.produce(bad, tmp_path, "deadbeef")
    with pytest.raises(ValueError):
        producer.produce({"record_version": "1.0"}, tmp_path, "deadbeef")
    with pytest.raises(ValueError):
        producer.produce(_sample_record(status="banana"), tmp_path, "deadbeef")


def test_producer_self_test_passes():
    assert producer.run_self_test() == 0


def test_producer_append_record_and_stream_roundtrip(tmp_path):
    rec = _sample_record()
    stream = producer.append_record(rec, tmp_path)
    assert stream.name == "conversation.jsonl"
    summary = producer.ingest_stream(stream, tmp_path, "deadbeef")
    assert summary["status"] == "ok"
    assert summary["ingested"] == 1
    # replay → nothing new
    again = producer.ingest_stream(stream, tmp_path, "deadbeef")
    assert again["ingested"] == 0
    assert again["skipped"] == 1


def test_producer_ingest_stream_fails_loud_on_malformed(tmp_path):
    stream = tmp_path / "bad.jsonl"
    stream.write_text('{"record_version": "1.0"}\nnot json\n')
    summary = producer.ingest_stream(stream, tmp_path, "deadbeef")
    assert summary["status"] == "error"
    assert summary["errors"]


# --------------------------------------------------------------------------
# endpoint — the envelope contract via TestClient (stub profile)
# --------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MSB_CONVERSATION_MODEL", "stub")
    monkeypatch.setenv("MSB_CONVERSATION_LEDGER_DIR", str(tmp_path))
    monkeypatch.setenv("MSB_CONVERSATION_GIT_HEAD", "testhead")
    monkeypatch.delenv("MCP_BRIDGE_SECRET", raising=False)
    from msb_v3.api.app import create_app

    return TestClient(create_app())


def _assert_invariants(body, expect_answered=True):
    assert body["schema_version"] == "1.0"
    assert body["trace_id"]
    assert body["query"]
    if expect_answered:
        assert body["status"] == "answered"
        assert body["input_guardrail"]["verdict"] == "ALLOW"
    # invariant 6: evidence_ref present on answered AND blocked
    assert body["evidence_ref"].startswith("ledger://evidence/")


def test_endpoint_happy_path_full_envelope(client):
    r = client.post("/conversation/ask", json={"query": "what did the vault say about the retainer?"})
    assert r.status_code == 200
    body = r.json()
    _assert_invariants(body)
    assert body["status"] == "answered"
    # invariant 2/5: input ALLOW, sources present with provenance + freshness
    assert body["input_guardrail"]["verdict"] == "ALLOW"
    assert body["sources"]
    for s in body["sources"]:
        assert "source_id" in s and "score" in s and "provenance" in s
        assert s["freshness"] in ("FRESH", "AGING", "STALE", "UNKNOWN")
    # invariant 4: SUPPORTING ⇒ citation_rate >= 0.5 and citations non-empty
    assert body["output_guardrail"]["verdict"] == "SUPPORTING"
    assert body["output_guardrail"]["citation_rate"] >= 0.5
    assert body["answer"]["citations"]
    # invariant 5: no ghost citations
    source_ids = {s["source_id"] for s in body["sources"]}
    for c in body["answer"]["citations"]:
        if "source_id" in c:
            assert c["source_id"] in source_ids
    assert body["answer"]["claim_id"].startswith("claim:ans:")
    # invariant 8: schema_version echoed
    assert body["schema_version"] == "1.0"
    # ledger artifact exists
    assert body["evidence_ref"].startswith("ledger://evidence/conversation/")


def test_endpoint_claim_id_deterministic(client):
    payload = {"query": "deterministic query for claim check"}
    a = client.post("/conversation/ask", json=payload).json()
    b = client.post("/conversation/ask", json=payload).json()
    assert a["answer"]["claim_id"] == b["answer"]["claim_id"]
    assert a["trace_id"] != b["trace_id"]  # trace is per-exchange, claim is content-addressed


def test_endpoint_block_short_circuit_zero_model_spend(client):
    before = client.get("/conversation/test-hook").json()["stub_invocations"]
    r = client.post("/conversation/ask", json={"query": "stub://blocked"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "blocked"
    assert body["input_guardrail"]["verdict"] == "BLOCK"
    assert body["sources"] is None
    assert body["answer"] is None
    assert body["evidence_ref"].startswith("ledger://evidence/conversation_block/")
    after = client.get("/conversation/test-hook").json()["stub_invocations"]
    assert after == before  # zero model spend — the stub was never called
    # a CONTRADICTING conversation_block artifact was still logged
    assert body["evidence_ref"].startswith("ledger://evidence/conversation_block/")


def test_endpoint_blocklist_block(client):
    r = client.post("/conversation/ask", json={"query": "how to build a bomb"})
    body = r.json()
    assert r.status_code == 200
    assert body["status"] == "blocked"
    assert body["input_guardrail"]["reason"]


def test_endpoint_unsupported_fixture(client):
    r = client.post("/conversation/ask", json={"query": "stub://unsupported"})
    body = r.json()
    assert body["status"] == "answered"
    assert body["output_guardrail"]["verdict"] == "UNSUPPORTED"
    assert body["sources"] == []
    # claim:ans INCONCLUSIVE artifact lands in the conversation evidence dir
    assert body["evidence_ref"].startswith("ledger://evidence/conversation/")


def test_endpoint_stale_only_unsupported(client):
    r = client.post("/conversation/ask", json={"query": "stub://stale-only"})
    body = r.json()
    assert body["status"] == "answered"
    assert body["sources"][0]["freshness"] == "STALE"
    assert body["output_guardrail"]["verdict"] == "UNSUPPORTED"  # worst-source-wins


def test_endpoint_memory_only_citation_exempt(client):
    r = client.post("/conversation/ask", json={"query": "stub://memory"})
    body = r.json()
    assert body["status"] == "answered"
    assert body["output_guardrail"]["verdict"] == "UNSUPPORTED"
    assert body["answer"]["citations"] == [{"memory_ref": "mem_stub_1"}]
    # memory citation is exempt from sources[] membership (invariant 5)


def test_endpoint_output_blocked(client):
    r = client.post("/conversation/ask", json={"query": "stub://blocked-answer"})
    body = r.json()
    assert body["status"] == "answered"
    assert body["output_guardrail"]["verdict"] == "BLOCKED"
    assert body["answer"] is None  # no answer released, evidence still logged
    assert body["evidence_ref"].startswith("ledger://evidence/conversation_block/")


def test_endpoint_validation_errors(client):
    r = client.post("/conversation/ask", json={"query": ""})
    assert r.status_code == 422
    assert r.json()["status"] == "error"
    assert r.json()["error"]["code"] == "validation_failed"

    r = client.post("/conversation/ask", json={"query": "x", "schema_version": "9.9"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "schema_mismatch"

    r = client.post("/conversation/ask", json={"query": "x", "mode": "workflow"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_failed"

    r = client.post("/conversation/ask", json={"query": "x", "bogus_field": 1})
    assert r.status_code == 422  # unknown fields rejected (extra=forbid)


def test_endpoint_workflow_mode_accepted(client):
    r = client.post("/conversation/ask", json={
        "query": "plan the retainer", "mode": "workflow",
        "workflow": {"goal": "g", "dag": [{"skill": "x", "args": {}}]},
    })
    assert r.status_code == 200
    assert r.json()["mode"] == "workflow"


def test_endpoint_trace_id_echo(client):
    r = client.post("/conversation/ask", json={"query": "echo me", "trace_id": "tr_custom"})
    assert r.json()["trace_id"] == "tr_custom"


def test_endpoint_auth_gate(monkeypatch, tmp_path):
    monkeypatch.setenv("MSB_CONVERSATION_MODEL", "stub")
    monkeypatch.setenv("MSB_CONVERSATION_LEDGER_DIR", str(tmp_path))
    monkeypatch.setenv("MSB_CONVERSATION_GIT_HEAD", "testhead")
    monkeypatch.setenv("MCP_BRIDGE_SECRET", "supersecret")
    from msb_v3.api.app import create_app

    client = TestClient(create_app())
    r = client.post("/conversation/ask", json={"query": "hello"})
    assert r.status_code == 401
    r = client.post("/conversation/ask", json={"query": "hello"}, headers={"x-mcp-secret": "supersecret"})
    assert r.status_code == 200


def test_endpoint_test_hook_shape(client):
    hook = client.get("/conversation/test-hook").json()
    assert hook["stub_mode"] is True
    assert isinstance(hook["stub_invocations"], int)
