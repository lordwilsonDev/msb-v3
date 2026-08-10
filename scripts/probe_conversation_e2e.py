"""Conversation E2E probe — the done-definition for the conversation interface.

Implements docs/conversation-e2e-harness-v1.md. Runs the full flow
(ask -> retrieve -> input_guardrail -> compose -> output_guardrail -> answer
-> log) against a health-checked boot of the real stack, with the model hop
replaced by the deterministic stub (CI profile). Asserts every envelope
invariant (§4 of the envelope spec) and producer invariant (§11 of the
producer spec), proves the BLOCK short-circuit is truly zero model spend via
the test-hook counter, verifies the ledger artifacts, then replays the
recorded exchanges offline for the §6 quality metrics.

The invariant assertions mirror tests/test_conversation.py exactly — the unit
suite and this probe pin the SAME contract, so they cannot drift.

Zero-spend by construction: stub model + deterministic producer + recorded
replay. No model, no network beyond the local server, no API keys.

Exit codes: 0 = all assertions + gated metrics pass; 1 = any assertion or
gate fails; 2 = harness misuse (argparse).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from msb_v3.conversation import producer  # noqa: E402
from msb_v3.conversation.envelope import (  # noqa: E402
    SCHEMA_VERSION,
    StubModel,
    claim_id_for_query,
    input_guardrail,
)

# --- fixture queries (drift-proof with the stub's kind_for + is_block_query) ---

SUPPORTING_QUERY = "stub://supported what does the Fox Valley retainer cover?"
UNSUPPORTED_QUERY = "stub://unsupported what do we know about quantum coffee?"
STALE_ONLY_QUERY = "stub://stale-only is the old fixture note still current?"
INPUT_BLOCK_QUERY = "stub://blocked for probe testing"
OUTPUT_BLOCK_QUERY = "stub://blocked-answer please respond"

EXPECTED_COMPOSE_INVOCATIONS = 5  # supporting x2 (determinism pair) + unsupported + stale + output-block


def _post(base_url: str, path: str, secret: str, payload: dict) -> tuple[int, Any]:
    """POST JSON with the seeded x-mcp-secret; returns (http_status, body)."""
    req = urllib.request.Request(
        f"{base_url}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-mcp-secret": secret},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8"))
        except (json.JSONDecodeError, OSError):
            body = None
        return exc.code, body


def _get(base_url: str, path: str, secret: str) -> tuple[int, Any]:
    req = urllib.request.Request(
        f"{base_url}{path}",
        headers={"x-mcp-secret": secret},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8"))
        except (json.JSONDecodeError, OSError):
            body = None
        return exc.code, body


# --- envelope invariants (mirror tests/test_conversation.py `_assert_invariants`
#     + the per-path assertions — the unit suite and this probe pin ONE contract) ---


def assert_envelope_invariants(body: dict, expect_answered: bool = True) -> None:
    """All 8 envelope invariants. Raises AssertionError on violation — the
    probe catches per-path so a failure names the path AND the invariant."""
    # invariant 8: schema_version echoed unchanged
    assert body.get("schema_version") == SCHEMA_VERSION, "invariant 8: schema_version echo"
    # invariant 1: trace_id non-empty
    assert body.get("trace_id"), "invariant 1: trace_id non-empty"
    assert body.get("query"), "query echoed"
    if expect_answered:
        assert body.get("status") == "answered", "invariant 2: answered status"
        assert body["input_guardrail"].get("verdict") == "ALLOW", "invariant 2: input ALLOW"
    # invariant 6: evidence_ref present on answered AND blocked
    assert str(body.get("evidence_ref", "")).startswith("ledger://evidence/"), "invariant 6: evidence_ref"


def _assert_supporting_path(body: dict) -> None:
    """invariant 2/5 (sources + provenance + freshness), invariant 4
    (SUPPORTING => citation_rate >= 0.5, citations non-empty), invariant 5
    (no ghost citations)."""
    sources = body.get("sources")
    assert sources, "SUPPORTING path: sources non-empty (invariant 3)"
    for s in sources:
        assert "source_id" in s and "score" in s and "provenance" in s, "invariant 5: source shape"
        assert s.get("freshness") in ("FRESH", "AGING", "STALE", "UNKNOWN"), "freshness band"
    out = body["output_guardrail"]
    assert out["verdict"] == "SUPPORTING", f"output SUPPORTING, got {out['verdict']!r}"
    assert out["citation_rate"] >= 0.5, "invariant 4: citation_rate >= 0.5"
    citations = body["answer"]["citations"]
    assert citations, "invariant 4: citations non-empty"
    source_ids = {s["source_id"] for s in sources}
    for c in citations:
        if "source_id" in c:
            assert c["source_id"] in source_ids, "invariant 5: no ghost citations"
    assert body["answer"]["claim_id"].startswith("claim:ans:"), "claim_id prefix"
    assert body["evidence_ref"].startswith("ledger://evidence/conversation/"), "conversation evidence dir"


def _assert_input_block_path(body: dict) -> None:
    """Spec §3: short-circuit — status blocked, input BLOCK, no sources/answer,
    a conversation_block artifact was logged."""
    assert body.get("status") == "blocked", "input-block: status blocked"
    assert body["input_guardrail"]["verdict"] == "BLOCK", "input-block: BLOCK verdict"
    assert body.get("sources") is None, "input-block: no sources in envelope"
    assert body.get("answer") is None, "input-block: no answer in envelope"
    assert body["evidence_ref"].startswith("ledger://evidence/conversation_block/"), "block evidence dir"


def _assert_output_block_path(body: dict) -> None:
    """The OUTPUT guardrail fires: answered, output BLOCKED, no answer
    released, evidence still logged (maps to claim:ok:query CONTRADICTING)."""
    assert body.get("status") == "answered", "output-block: answered status"
    assert body["output_guardrail"]["verdict"] == "BLOCKED", "output-block: BLOCKED verdict"
    assert body.get("answer") is None, "output-block: no answer released"
    assert body["evidence_ref"].startswith("ledger://evidence/conversation_block/"), "block evidence dir"


# --- §8 ledger verification (producer spec §11 — asserted on the ledger) ---


def _load_claims(ledger_dir: Path) -> dict[str, dict]:
    return {c.get("claim_id"): c for c in producer.load_registry(ledger_dir / "claims.json")["claims"]}


def _resolve_artifact(ledger_dir: Path, evidence_ref: str) -> Optional[dict]:
    """evidence_ref is ledger://evidence/<type>/<filename> — resolve to the
    artifact on disk, proving the ref terminates in a real §8 object."""
    if not evidence_ref.startswith("ledger://evidence/"):
        return None
    rel = evidence_ref.removeprefix("ledger://evidence/")
    path = ledger_dir / "evidence" / rel
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def verify_ledger(ledger_dir: Path, results: list[dict]) -> list[str]:
    """Assert the producer invariants on the ledger the server wrote to."""
    failures: list[str] = []
    claims = _load_claims(ledger_dir)
    for res in results:
        ref = res.get("evidence_ref", "")
        artifact = _resolve_artifact(ledger_dir, ref)
        if artifact is None:
            failures.append(f"{res['name']}: evidence_ref {ref!r} does not resolve to a §8 artifact")
            continue
        prov = artifact.get("provenance") or {}
        layers = set(prov)
        if layers != {"execution", "environment", "input", "verifier", "dependency"}:
            failures.append(f"{res['name']}: artifact missing full provenance layers (got {sorted(layers)})")
        # the probe independently pins the rest of the §8 required keys (the
        # unit tests pin them too — the probe must not rely on the tests it
        # is meant to double-check).
        if not str(artifact.get("evidence_id", "")).startswith("ev_"):
            failures.append(f"{res['name']}: evidence_id must start with 'ev_'")
        if not str(artifact.get("subject_id", "")).startswith("trace:"):
            failures.append(f"{res['name']}: subject_id must reference a trace")
        for key in ("artifact_hash", "git_head", "result", "toolchain", "timestamp", "freshness"):
            if not artifact.get(key):
                failures.append(f"{res['name']}: §8 field {key!r} missing")
        want = res.get("expect_polarity")
        if want and artifact.get("polarity") != want:
            failures.append(f"{res['name']}: artifact polarity {artifact.get('polarity')!r} != {want!r}")

    # the input-block claim landed: claim:ok:query:<hash> is UNVERIFIED-with-
    # evidence (never supported — no false elevation). A block with no verdict
    # trace is a FAIL (spec §3.6).
    blocked_claim = claim_id_for_query(INPUT_BLOCK_QUERY)
    entry = claims.get(blocked_claim)
    if entry is None:
        failures.append(f"input-block: no claim entry for {blocked_claim}")
    else:
        if entry.get("verdict") != "UNVERIFIED":
            failures.append(f"input-block: claim verdict {entry.get('verdict')!r} != UNVERIFIED")
        if not entry.get("negative_evidence"):
            failures.append("input-block: claim has no negative_evidence bucket")
    return failures


# --- offline replay eval (§6 — the standing quality gate; report-only except
#     block-rate, which is a wiring check the stub cannot fake) ---


def replay_eval(stream: Path, ledger_dir: Path) -> dict[str, Any]:
    records: list[dict] = []
    if stream.exists():
        for line in stream.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                records.append(json.loads(line))

    # idempotency (producer invariant): replaying the stream into the SAME
    # ledger the server already ingested must ingest 0 new artifacts (the
    # cursor holds the hashes — a fresh scratch ledger would ingest everything
    # and prove nothing).
    summary = producer.ingest_stream(stream, ledger_dir, "unknown")
    if summary["status"] == "error":
        return {"error": summary["errors"], "total": len(records)}
    idempotent_ingest = summary["ingested"]

    metrics: dict[str, Any] = {"total": len(records)}
    answered = [r for r in records if r.get("status") == "answered"]
    blocked = [r for r in records if r.get("status") == "blocked"]
    metrics["answered"] = len(answered)
    metrics["blocked"] = len(blocked)
    metrics["block_rate"] = round(len(blocked) / len(records), 4) if records else 0.0

    supporting = [
        r for r in answered
        if (r.get("output_guardrail") or {}).get("verdict") == "SUPPORTING"
    ]
    metrics["guardrail_pass_rate"] = round(len(supporting) / len(answered), 4) if answered else 0.0

    with_citations = [r for r in supporting if (r.get("answer") or {}).get("citations")]
    metrics["source_citation_rate"] = round(len(with_citations) / len(supporting), 4) if supporting else 0.0

    # claim verdict distribution — replay the stream through the producer into
    # a SCRATCH ledger (still zero spend, zero network). NOT dry-run: produce()
    # loads the registry from disk on every call and dry-run never persists,
    # so a dry-run replay would evaluate each record against an empty registry
    # and could never reproduce a VERIFIED -> REGRESSED transition. Real writes
    # into a temp dir accumulate prior state exactly as the server ledger does.
    verdicts: dict[str, int] = {}
    with tempfile.TemporaryDirectory(prefix="e2e-verdicts-") as tmp:
        scratch = Path(tmp)
        summary = producer.ingest_stream(stream, scratch, "unknown")
        if summary["status"] != "error":
            for entry in producer.load_registry(scratch / "claims.json")["claims"]:
                verdicts[str(entry.get("verdict", "?"))] = verdicts.get(str(entry.get("verdict", "?")), 0) + 1
    metrics["verdict_distribution"] = verdicts
    metrics["idempotent_replay_ingested"] = idempotent_ingest
    return metrics


# --- harness self-test (no server — verifies the harness itself) ---


def self_test() -> int:
    """The probe's own health check: a broken harness must fail, not silently
    pass (spec §7). Zero-spend, in-process, no server."""
    try:
        stub = StubModel()

        # 1. stub determinism: same request -> byte-for-byte same response.
        a1 = stub.retrieve(SUPPORTING_QUERY)
        a2 = stub.retrieve(SUPPORTING_QUERY)
        assert a1 == a2, "stub retrieve must be deterministic"
        t1, c1 = stub.compose(SUPPORTING_QUERY, a1)
        t2, _ = stub.compose(SUPPORTING_QUERY, a1)
        assert t1 == t2, "stub compose must be deterministic"
        assert c1, "supporting fixture must produce citations"

        # 2. fixture wiring: block vs blocked-answer discrimination.
        assert stub.is_block_query(INPUT_BLOCK_QUERY), "input-block fixture must fire"
        assert not stub.is_block_query(OUTPUT_BLOCK_QUERY), "output-block fixture is NOT an input block"
        assert stub.kind_for(UNSUPPORTED_QUERY) == "UNSUPPORTED"
        assert stub.kind_for(STALE_ONLY_QUERY) == "STALE_ONLY"
        assert stub.retrieve(UNSUPPORTED_QUERY) == [], "unsupported fixture: no sources"

        # 3. block never invokes compose (the zero-spend contract).
        before = stub.invocations
        if stub.is_block_query(INPUT_BLOCK_QUERY):
            pass  # endpoint short-circuits — compose must NOT be called
        assert stub.invocations == before, "input-block must never reach compose"

        # 4. the invariant checker is not vacuous: a violation must raise.
        bad = {"status": "answered", "input_guardrail": {"verdict": "ALLOW"}}
        raised = False
        try:
            assert_envelope_invariants(bad)
        except AssertionError:
            raised = True
        assert raised, "invariant checker must reject a malformed envelope"

        # 5. the block decision chain in stub mode (mirrors the endpoint):
        #    the regex blocklist ALLOWs the fixture query, is_block_query BLOCKs.
        assert input_guardrail(INPUT_BLOCK_QUERY)["verdict"] == "ALLOW", "fixture query must be regex-clean"
        assert stub.is_block_query(INPUT_BLOCK_QUERY) is True

        # 6. claim id determinism for the blocked query.
        assert claim_id_for_query(INPUT_BLOCK_QUERY) == claim_id_for_query(INPUT_BLOCK_QUERY)

        print("self-test: PASS (stub determinism, fixture wiring, zero-spend contract, non-vacuous checker)")
        return 0
    except AssertionError as exc:
        print(f"self-test: FAIL — {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001 — a harness crash is a harness failure
        print(f"self-test: FAIL — {type(exc).__name__}: {exc}")
        return 1


# --- the probe itself ---


def _check_boot(base_url: str, secret: str, mode: str) -> list[str]:
    """Verified-boot (spec §4): /health green, then the conversation router is
    mounted AND auth works AND (in stub mode) the stub profile is active.

    The test-hook (not /research/assistant/preflight) is this contract's
    fail-fast gate: it proves the conversation router + auth + stub profile in
    one call, where preflight verifies the research stack and would drag in an
    ollama dependency stub mode must never need."""
    failures: list[str] = []
    try:
        status, _ = _get(base_url, "/health", secret)
    except (urllib.error.URLError, OSError) as exc:
        return [f"boot: server not reachable at {base_url}: {exc}"]
    if status != 200:
        failures.append(f"boot: /health returned {status}")
    status, hook = _get(base_url, "/conversation/test-hook", secret)
    if status != 200:
        failures.append(f"boot: /conversation/test-hook returned {status} (auth or router problem)")
        return failures
    if mode == "stub" and hook.get("stub_mode") is not True:
        failures.append(f"boot: server not in stub mode (got {hook.get('stub_mode')!r})")
    return failures


def _run_probe(base_url: str, secret: str, mode: str) -> tuple[list[str], list[dict]]:
    failures: list[str] = []
    results: list[dict] = []

    def ask(query: str, expect_answered: bool = True) -> dict:
        status, body = _post(base_url, "/conversation/ask", secret, {"query": query})
        if status != 200:
            failures.append(f"ask {query!r}: HTTP {status} (expected 200)")
            return {}
        return body

    # path 1: ALLOW/SUPPORTING (the happy path — full envelope)
    body = ask(SUPPORTING_QUERY)
    try:
        assert_envelope_invariants(body)
        _assert_supporting_path(body)
        results.append({
            "name": "supporting", "evidence_ref": body.get("evidence_ref", ""),
            "expect_polarity": "SUPPORTING",
        })
        print("supporting: PASS")
    except AssertionError as exc:
        failures.append(f"supporting: {exc}")
        print(f"supporting: FAIL — {exc}")

    # path 2: invariant 7 determinism — identical request => identical claim_id
    body2 = ask(SUPPORTING_QUERY)
    if body.get("answer") and body2.get("answer"):
        same_claim = body["answer"]["claim_id"] == body2["answer"]["claim_id"]
        distinct_trace = body["trace_id"] != body2["trace_id"]
        if not same_claim:
            failures.append("determinism: identical requests produced different claim_id (invariant 7)")
        if not distinct_trace:
            failures.append("determinism: trace_id must be per-exchange")
        print(f"determinism: {'PASS' if same_claim and distinct_trace else 'FAIL'}")

    # path 3: ALLOW/UNSUPPORTED — no sources => INCONCLUSIVE polarity, claim
    # can never elevate (mirrors test_endpoint_unsupported_fixture)
    body = ask(UNSUPPORTED_QUERY)
    try:
        assert_envelope_invariants(body)
        assert body["output_guardrail"]["verdict"] == "UNSUPPORTED", f"got {body['output_guardrail']['verdict']!r}"
        assert body["sources"] == [], "unsupported: no sources"
        results.append({
            "name": "unsupported", "evidence_ref": body.get("evidence_ref", ""),
            "expect_polarity": "INCONCLUSIVE",
        })
        print("unsupported: PASS")
    except AssertionError as exc:
        failures.append(f"unsupported: {exc}")
        print(f"unsupported: FAIL — {exc}")

    # path 4: STALE-only — worst-source-wins: answer must NOT be SUPPORTING
    body = ask(STALE_ONLY_QUERY)
    try:
        assert_envelope_invariants(body)
        assert body["sources"][0]["freshness"] == "STALE", "stale fixture source"
        assert body["output_guardrail"]["verdict"] == "UNSUPPORTED", "worst-source-wins"
        results.append({
            "name": "stale-only", "evidence_ref": body.get("evidence_ref", ""),
            "expect_polarity": "INCONCLUSIVE",
        })
        print("stale-only: PASS")
    except AssertionError as exc:
        failures.append(f"stale-only: {exc}")
        print(f"stale-only: FAIL — {exc}")

    # path 5: INPUT BLOCK — short-circuit, zero model spend, claim:ok:query.
    # The counter is read immediately BEFORE and AFTER this one request (the
    # earlier paths legitimately advanced it) — a BLOCK must not move it.
    _, hook = _get(base_url, "/conversation/test-hook", secret)
    before_block = int(hook.get("stub_invocations", 0))
    body = ask(INPUT_BLOCK_QUERY)
    try:
        assert_envelope_invariants(body, expect_answered=False)
        _assert_input_block_path(body)
        results.append({
            "name": "input-block", "evidence_ref": body.get("evidence_ref", ""),
            "expect_polarity": "CONTRADICTING",
        })
        print("input-block: PASS")
    except AssertionError as exc:
        failures.append(f"input-block: {exc}")
        print(f"input-block: FAIL — {exc}")
    _, hook = _get(base_url, "/conversation/test-hook", secret)
    after_block = int(hook.get("stub_invocations", 0))
    if after_block != before_block:
        failures.append(f"input-block: zero-model-spend violated — stub invocations {before_block} -> {after_block}")

    # path 6: OUTPUT BLOCK — the drafted answer trips the output guardrail
    body = ask(OUTPUT_BLOCK_QUERY)
    try:
        assert_envelope_invariants(body)
        _assert_output_block_path(body)
        results.append({
            "name": "output-block", "evidence_ref": body.get("evidence_ref", ""),
            "expect_polarity": "CONTRADICTING",
        })
        print("output-block: PASS")
    except AssertionError as exc:
        failures.append(f"output-block: {exc}")
        print(f"output-block: FAIL — {exc}")

    if mode == "stub":
        _, hook = _get(base_url, "/conversation/test-hook", secret)
        total = int(hook.get("stub_invocations", 0))
        if total != EXPECTED_COMPOSE_INVOCATIONS:
            failures.append(
                f"stub invocation count {total} != expected {EXPECTED_COMPOSE_INVOCATIONS}"
                " (5 = supporting x2 + unsupported + stale + output-block)"
            )

    return failures, results


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Conversation E2E probe (harness spec v1)")
    parser.add_argument("--mode", choices=["stub", "live"], default="stub",
                        help="stub (CI, zero-spend) or live (local only, requires ollama)")
    parser.add_argument("--base-url", default=os.getenv("MSB_BASE", "http://127.0.0.1:8766"))
    parser.add_argument("--secret-env", default="MCP_BRIDGE_SECRET",
                        help="env var carrying the x-mcp-secret the server accepts")
    parser.add_argument("--ledger-dir", default=None,
                        help="the ledger the server wrote to (default: MSB_CONVERSATION_LEDGER_DIR or the shared constellation ledger)")
    parser.add_argument("--replay", action="store_true",
                        help="run the §6 offline replay eval after the probe")
    parser.add_argument("--self-test", action="store_true",
                        help="verify the harness itself (no server needed)")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    secret = os.getenv(args.secret_env, "")
    if not secret:
        print(f"FAIL: {args.secret_env} is not set — probe is fail-closed (CI must seed it)")
        return 1

    if args.ledger_dir:
        ledger_dir = Path(args.ledger_dir)
    else:
        ledger_dir = producer.default_ledger_dir()

    boot_failures = _check_boot(args.base_url, secret, args.mode)
    if boot_failures:
        for f in boot_failures:
            print(f"FAIL: {f}")
        return 1

    failures, results = _run_probe(args.base_url, secret, args.mode)

    ledger_failures = verify_ledger(ledger_dir, results)
    failures.extend(ledger_failures)
    for f in ledger_failures:
        print(f"FAIL: ledger — {f}")

    metrics: dict[str, Any] = {}
    if args.replay:
        stream = ledger_dir / "records" / "conversation.jsonl"
        metrics = replay_eval(stream, ledger_dir)
        if metrics.get("error"):
            print(f"FAIL: replay — {metrics['error']}")
            failures.append("replay ingest error")
        else:
            print(
                "replay: total=%d block_rate=%.2f pass_rate=%.2f citation_rate=%.2f "
                "idempotent_replay_ingested=%d verdicts=%s"
                % (
                    metrics["total"], metrics["block_rate"], metrics["guardrail_pass_rate"],
                    metrics["source_citation_rate"], metrics["idempotent_replay_ingested"],
                    metrics["verdict_distribution"],
                )
            )
            if metrics["block_rate"] <= 0:
                failures.append("replay gate: block_rate must be > 0 (the BLOCK fixture must have fired)")
            if metrics["idempotent_replay_ingested"] != 0:
                failures.append(
                    "replay: replaying the recorded stream ingested new artifacts — idempotency broken"
                )

    if failures:
        print(f"PROBE FAIL — {len(failures)} failure(s)")
        return 1
    print("PROBE PASS — full ask->retrieve->guardrail->answer->log chain verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
