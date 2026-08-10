"""Cross-producer REGRESS test — the loop the Task Contract was built for.

Two producers, one ledger, one claim namespace:

  1. msb-v3's task executor fails a task under its contract and emits a
     pinned TASK_FAILED event (records/task_events.jsonl) + its own §8
     CONTRADICTING evidence on the availability claim claim:ok:task:<id>.
  2. The sovereign-verification replay consumer (a producer adapter living in
     the hermes skill tree) ingests that same event stream as NEGATIVE
     evidence, deriving claim:ok:task:<id> byte-identically.

When the availability claim previously held SUPPORTING evidence (the task
used to complete), the fresh contradiction flips it to REGRESSED — historical
green + current red, preserved. This test proves the collision (one claim
across producers), the verdict agreement, the §8 artifact round-trip, and
consumer idempotency.

The consumer lives outside this repo (~/.hermes/skills/sovereign-verification)
— the test SKIPS when that tree is absent (e.g. GitHub runners), so the
inherently cross-machine loop stays honest instead of failing artificially.
Point SV_REPLAY_CONSUMER at the script to relocate it.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from msb_v3.conversation import executor, task_producer  # noqa: E402

CONSUMER = Path(
    os.getenv(
        "SV_REPLAY_CONSUMER",
        str(Path.home() / ".hermes" / "skills" / "sovereign-verification" / "scripts" / "replay_feedback_events.py"),
    )
).expanduser()


@pytest.fixture(scope="module")
def consumer():
    """Load the replay consumer by file path (no sys.path mutation)."""
    if not CONSUMER.exists():
        pytest.skip(
            f"sovereign-verification replay consumer not found at {CONSUMER} — "
            "cross-producer test requires both producers (local hermes tree)"
        )
    spec = importlib.util.spec_from_file_location("replay_feedback_events", str(CONSUMER))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_claim_ok_task_derivation_is_verbatim():
    """The pinned collision: both producers derive claim:ok:task:<task_id>
    VERBATIM (no hashing, no sanitization) — or REGRESS can never fire."""
    assert task_producer.claim_ok_task("crossprod-1") == "claim:ok:task:crossprod-1"
    assert task_producer.claim_ok_task("t/with:chars") == "claim:ok:task:t/with:chars"


def test_executor_failure_regresses_via_consumer(consumer, tmp_path):
    ledger = tmp_path / "ledger"
    out = tmp_path / "out"
    out.mkdir()

    # --- seed the prior SUPPORTING state: the task used to complete ---
    ledger.mkdir(parents=True, exist_ok=True)
    claims = {"claims": [{
        "claim_id": "claim:ok:task:crossprod-1",
        "subject": "task:crossprod-1",
        "text": '"task:crossprod-1" completes without failure',
        "verification_tier": "T3",
        "verdict": "VERIFIED",
        "supporting_evidence": ["ev_prior_success"],
        "negative_evidence": [],
        "first_negative_evidence_at": None,
        "last_negative_evidence_at": None,
    }], "generated_at": None}
    (ledger / "claims.json").write_text(json.dumps(claims, indent=2) + "\n", encoding="utf-8")

    # --- emit TASK_FAILED via the REAL executor (budget failure -> claim:ok) ---
    contract = {
        "task_id": "crossprod-1", "objective": "keep the service up", "skill": "stub",
        "constraints": {"budget_cap_usd": 0.01}, "args": {"cost_usd": 0.5},
        "status": "READY",
    }
    r = executor.execute_contract(
        contract, runner=executor.StubRunner(out), output_root=out,
        ledger_dir=ledger, git_head="deadbeef", goal="uptime",
    )
    assert r.status == "FAILED" and r.failure_kind == "budget"
    assert r.claim_id == "claim:ok:task:crossprod-1", "availability claim, byte-identical"
    assert r.verdict == "REGRESSED", "executor-side: seeded VERIFIED + fresh contradiction"
    assert r.event_ref is not None
    events_path = ledger / "records" / "task_events.jsonl"
    assert events_path.exists(), "executor must emit the TASK_FAILED stream"

    # --- the collision the design rests on ---
    ev = json.loads(events_path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert consumer.derive_claim(ev)["claim_id"] == \
        task_producer.claim_ok_task("crossprod-1") == "claim:ok:task:crossprod-1"

    # --- run the sovereign-verification replay consumer against the stream ---
    summary = consumer.ingest(events_path, ledger, "deadbeef")
    assert summary["status"] == "ok" and summary["ingested"] == 1, summary

    claim = next(
        c for c in json.loads((ledger / "claims.json").read_text(encoding="utf-8"))["claims"]
        if c["claim_id"] == "claim:ok:task:crossprod-1"
    )
    assert claim["verdict"] == "REGRESSED"
    assert len(claim["negative_evidence"]) == 2, \
        "both producers' contradicting artifacts accumulate (executor + consumer)"
    # the historical support linkage MUST survive the contradiction — a
    # REGRESSED claim shows what WAS supported (blueprint: contradictory
    # evidence is never silently discarded)
    assert claim.get("supporting_evidence") == ["ev_prior_success"], claim
    assert claim["first_negative_evidence_at"], "first negative evidence recorded"
    assert claim["last_negative_evidence_at"] == ev["ts"], "consumer stamps the event ts"

    # --- the consumer's §8 artifact round-trips the executor's failure text ---
    negative = list((ledger / "evidence" / "negative").glob("*.json"))
    assert len(negative) == 1
    art = json.loads(negative[0].read_text(encoding="utf-8"))
    assert art["polarity"] == "CONTRADICTING"
    assert art["evidence_type"] == "task_failed"
    assert "budget" in art["provenance"]["input"]["error"], \
        "executor's failure kind round-tripped"
    # the executor's OWN artifact is on disk too (two producers, both real)
    assert len(list((ledger / "evidence" / "task_failed").glob("*.json"))) == 1

    # --- idempotent: replaying the same stream ingests nothing new ---
    again = consumer.ingest(events_path, ledger, "deadbeef")
    assert again["ingested"] == 0 and again["skipped"] == 1, again
