"""Task Contract → Ledger Evidence Producer — §8 artifacts for contract executions.

Mirrors producer.py (the conversation producer) discipline-for-discipline:
one evidence schema, one claims registry, one replay cursor, content-addressed
idempotency, fail-loud. Two producers, one ledger (docs/task-contract-v1.md §8).

Evidence types added by this producer:

| outcome                          | polarity      | claim                    | type               |
|----------------------------------|---------------|--------------------------|--------------------|
| VERIFIED                         | SUPPORTING    | claim:done:task:<hash>   | task_verified      |
| SUBMITTED (no expected_output)   | INCONCLUSIVE  | claim:done:task:<hash>   | task_submitted     |
| FAILED (predicates fail)         | CONTRADICTING | claim:done:task:<hash>   | task_failed_verify |
| FAILED (crash/budget/…)/ROLLED_BACK | CONTRADICTING | claim:ok:task:<task_id>  | task_failed        |

claim:ok:task:<task_id> is byte-identical to the replay consumer's derivation,
so a TASK_FAILED event carrying the same task_id collides on this id and the
existing REGRESS detection fires (spec §8). Deterministic, zero-spend, stdlib.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from msb_v3.conversation import (
    producer as _conv,  # shared registry/cursor/verdict machinery
)
from msb_v3.conversation.envelope import canonical_json
from msb_v3.conversation.task_contract import claim_done_task, claim_ok_task

TOOLCHAIN = "msb-v3/task-contract"
TOOL_VERSION = "1.0"
TIER = "T3"

POLARITY_SUPPORTING = "SUPPORTING"
POLARITY_INCONCLUSIVE = "INCONCLUSIVE"
POLARITY_CONTRADICTING = "CONTRADICTING"

VALID_OUTCOMES = ("VERIFIED", "SUBMITTED", "FAILED", "ROLLED_BACK")
VALID_FAILURE_KINDS = (
    "predicates", "crash", "budget", "steps", "permission", "scope", "rollback_failed",
)

_REQUIRED_RECORD_FIELDS = (
    "record_version", "record_type", "task_id", "contract", "outcome",
    "exit_code", "git_head", "recorded_at",
)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sanitize(name: str) -> str:
    return "".join("_" if ch in ":+/\\ " else ch for ch in name)


def record_identity(record: dict) -> str:
    """Content-addressed dedupe identity on the RECORD (same-second distinct
    executions of the same contract produce distinct hashes; an identical
    re-execution is a true duplicate regardless of when it ran)."""
    return _sha256(canonical_json(record))


def _done_claim(contract: dict) -> str:
    """claim:done:task:<hash> — content-addressed on the CONTRACT (task_id +
    expected_output + predicates), never on execution state (spec §8)."""
    expected = contract.get("expected_output")
    predicates = (expected or {}).get("predicates", []) if isinstance(expected, dict) else []
    return claim_done_task(str(contract.get("task_id", "")), expected, predicates)


def _polarity_mapping(record: dict) -> tuple[str, str, str]:
    """§8 mapping — the ONLY path to an artifact."""
    outcome = str(record.get("outcome", ""))
    contract = record.get("contract") or {}
    if outcome == "VERIFIED":
        return POLARITY_SUPPORTING, "task_verified", _done_claim(contract)
    if outcome == "SUBMITTED":
        return POLARITY_INCONCLUSIVE, "task_submitted", _done_claim(contract)
    if outcome == "FAILED" and record.get("failure_kind") == "predicates":
        return POLARITY_CONTRADICTING, "task_failed_verify", _done_claim(contract)
    # crash / budget / steps / permission / scope / rollback_failed, and
    # ROLLED_BACK — all CONTRADICTING on the availability claim.
    return POLARITY_CONTRADICTING, "task_failed", claim_ok_task(str(record.get("task_id", "")))


def _validate_record(record: dict) -> None:
    """Fail loud on malformed records — never a silent no-op."""
    if not isinstance(record, dict):
        raise ValueError("task record must be a dict")
    missing = [f for f in _REQUIRED_RECORD_FIELDS if f not in record]
    if missing:
        raise ValueError(f"malformed task record: missing {', '.join(missing)}")
    if str(record.get("record_version")) != "1.0":
        raise ValueError(f"unsupported record_version: {record.get('record_version')!r}")
    if record.get("record_type") != "task":
        raise ValueError(f"record_type must be 'task', got {record.get('record_type')!r}")
    if record.get("outcome") not in VALID_OUTCOMES:
        raise ValueError(f"invalid outcome {record.get('outcome')!r}")
    kind = record.get("failure_kind")
    if kind is not None and kind not in VALID_FAILURE_KINDS:
        raise ValueError(f"invalid failure_kind {kind!r}")
    if not record.get("task_id") or not record.get("git_head"):
        raise ValueError("task record requires non-empty task_id and git_head")
    if not isinstance(record.get("contract"), dict):
        raise ValueError("task record requires a contract object")


def _self_consistency_check(record: dict) -> str:
    """Recompute the claim_id from the record's contract and verify it matches
    the executor-supplied claim_id. Mismatch ⇒ fail loud — never a mismatched
    attachment (mirrors the conversation producer's tamper guard)."""
    expected = record.get("claim_id")
    actual = _polarity_mapping(record)[2]
    if expected and expected != actual:
        raise ValueError(
            f"self-consistency failure: record claim_id {expected!r} != recomputed {actual!r}"
        )
    return actual


def build_evidence_artifact(record: dict, git_head: str) -> dict[str, Any]:
    """The §8 canonical evidence object — same top-level keys and five
    provenance layers as the conversation producer, so the ledger has ONE
    schema. git_head derives from the RECORD (record-is-source-of-truth)."""
    git_head = str(record.get("git_head") or git_head)
    record_hash = _sha256(canonical_json(record))
    evidence_id = f"ev_{record_hash[:12]}"
    polarity, ev_type, claim_id = _polarity_mapping(record)
    contract = record.get("contract") or {}
    expected = contract.get("expected_output")
    preds = (expected or {}).get("predicates", []) if isinstance(expected, dict) else []
    outcomes = record.get("predicate_outcomes") or []

    return {
        "evidence_id": evidence_id,
        "subject_id": f"task:{record.get('task_id', '')}",
        "claim_id": claim_id,
        "evidence_type": ev_type,
        "polarity": polarity,
        "git_head": git_head,
        "artifact_hash": record_hash[:16],
        "toolchain": TOOLCHAIN,
        "timestamp": str(record.get("recorded_at", "")),
        "result": record.get("outcome"),
        "provenance": {
            "execution": {
                "exit_code": record.get("exit_code"),
                "outcome": record.get("outcome"),
                "failure_kind": record.get("failure_kind"),
                "predicates_passed": sum(1 for o in outcomes if o.get("passed")),
                "predicates_total": len(outcomes),
                "rollback": (record.get("rollback") or {}).get("kind"),
            },
            "environment": {
                "tenant_id": record.get("tenant_id"),
                "git_head": git_head,
            },
            "input": {
                "task_id": record.get("task_id"),
                "expected_output": expected,
                "predicates": preds,
                "args": contract.get("args"),
            },
            "verifier": {
                "tool": TOOLCHAIN,
                "version": TOOL_VERSION,
                "predicate_outcomes": [
                    {"kind": o.get("kind"), "passed": o.get("passed")} for o in outcomes
                ],
            },
            "dependency": {
                "inputs": contract.get("inputs"),
                "preconditions": contract.get("preconditions"),
                "side_effects": contract.get("side_effects"),
            },
        },
        "freshness": "FRESH",
    }


def artifact_filename(record: dict, artifact: dict) -> str:
    ts = _sanitize(str(record.get("recorded_at", "")))
    return f"{ts}_{artifact['evidence_id']}.json"


def evidence_ref(record: dict, artifact: dict) -> str:
    return f"ledger://evidence/{artifact['evidence_type']}/{artifact_filename(record, artifact)}"


def _claim_entry(
    claim_id: str, record: dict, artifact: dict, prior: Optional[dict],
) -> dict[str, Any]:
    polarity = artifact["polarity"]
    verdict = _conv._verdict_for(prior, polarity)
    ts = str(record.get("recorded_at", ""))
    ev_id = artifact["evidence_id"]
    task_id = str(record.get("task_id", ""))
    entry: dict[str, Any] = {
        "claim_id": claim_id,
        "subject": f"task:{task_id}",
        "text": (
            f'task "{task_id}" produces its declared output'
            if claim_id.startswith("claim:done:")
            else f'task "{task_id}" completes without failure'
        ),
        "claim_type": "task_done" if claim_id.startswith("claim:done:") else "task_ok",
        "verification_tier": TIER,
        "verdict": verdict,
        "supporting_evidence": list((prior or {}).get("supporting_evidence", [])),
        "inconclusive_evidence": list((prior or {}).get("inconclusive_evidence", [])),
        "negative_evidence": list((prior or {}).get("negative_evidence", [])),
        "first_negative_evidence_at": (prior or {}).get("first_negative_evidence_at"),
        "last_negative_evidence_at": (prior or {}).get("last_negative_evidence_at"),
    }
    if polarity == POLARITY_SUPPORTING:
        entry["supporting_evidence"] = sorted({*entry["supporting_evidence"], ev_id})
        if not (prior or {}).get("first_supporting_evidence_at"):
            entry["first_supporting_evidence_at"] = ts
        entry["last_supporting_evidence_at"] = ts
    elif polarity == POLARITY_INCONCLUSIVE:
        entry["inconclusive_evidence"] = sorted({*entry["inconclusive_evidence"], ev_id})
        entry["last_inconclusive_evidence_at"] = ts
    else:
        entry["negative_evidence"] = sorted({*entry["negative_evidence"], ev_id})
        entry["first_negative_evidence_at"] = (prior or {}).get("first_negative_evidence_at") or ts
        entry["last_negative_evidence_at"] = ts
    return entry


def produce(
    record: dict,
    ledger_dir: Path,
    git_head: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """produce(record, ledger_dir, git_head, dry_run=False) -> artifact ref + claims state.

    Same contract as the conversation producer: deterministic, idempotent
    (cursor), fail-loud, dry_run computes without writing.
    """
    _validate_record(record)
    claim_id = _self_consistency_check(record)
    artifact = build_evidence_artifact(record, git_head)
    filename = artifact_filename(record, artifact)
    ref = evidence_ref(record, artifact)
    record_hash = record_identity(record)

    cursor_file = ledger_dir / "replay_cursor.json"
    claims_file = ledger_dir / "claims.json"
    out_dir = ledger_dir / "evidence" / artifact["evidence_type"]

    processed = _conv.load_cursor(cursor_file)
    registry = _conv.load_registry(claims_file)
    by_id = {c.get("claim_id"): c for c in registry["claims"]}

    already = record_hash in processed
    if already and not dry_run:
        prior = by_id.get(claim_id)
        entry = _claim_entry(claim_id, record, artifact, prior) if prior else None
        return {
            "evidence_ref": ref, "filename": filename, "artifact": artifact,
            "claim_id": claim_id,
            "verdict": entry.get("verdict") if entry else (prior or {}).get("verdict"),
            "ingested": False,
        }

    if not dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / filename
        if not out.exists():
            out.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")

    prior = by_id.get(claim_id)
    entry = _claim_entry(claim_id, record, artifact, prior)
    if prior is None:
        registry["claims"].append(entry)
    else:
        idx = next(i for i, c in enumerate(registry["claims"]) if c.get("claim_id") == claim_id)
        registry["claims"][idx] = entry
    processed.add(record_hash)

    if not dry_run:
        registry["claims"].sort(key=lambda c: c.get("claim_id", ""))
        registry["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        claims_file.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")
        cursor_file.write_text(
            json.dumps(
                {"processed": sorted(processed), "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")},
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    return {
        "evidence_ref": ref, "filename": filename, "artifact": artifact,
        "claim_id": claim_id, "verdict": entry["verdict"], "ingested": True,
    }


def append_task_event(event: dict, ledger_dir: Path) -> Path:
    """Append a TASK_FAILED event to the feedback stream the replay consumer
    ingests (`<ledger>/records/task_events.jsonl`, consumable via --stream).
    The shape is PINNED (spec §8): an event missing any parsed field is
    silently skipped by the consumer, so malformed events fail loud HERE."""
    if not isinstance(event, dict) or event.get("event") != "TASK_FAILED":
        raise ValueError("TASK_FAILED event requires event='TASK_FAILED'")
    if not event.get("task_id") or not event.get("version"):
        raise ValueError("TASK_FAILED event requires task_id and version")
    for field in ("failed_step", "failed_index", "error", "attempt", "max_attempts", "decision", "dag", "revised_dag", "ts"):
        if field not in event:
            raise ValueError(f"TASK_FAILED event missing pinned field {field!r}")
    records_dir = ledger_dir / "records"
    records_dir.mkdir(parents=True, exist_ok=True)
    stream = records_dir / "task_events.jsonl"
    with stream.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, sort_keys=True, ensure_ascii=False) + "\n")
    return stream


# --- ledger location + registry access: the SAME shared ledger as the
# conversation producer (one registry, one cursor, two producers) ---

default_ledger_dir = _conv.default_ledger_dir
default_git_head = _conv.default_git_head
load_registry = _conv.load_registry


def run_self_test() -> int:
    """Zero-spend in-memory verification of the task producer (spec §10)."""
    import tempfile

    def record(task_id="t.1", outcome="VERIFIED", kind=None, expected=None, preds=None):
        contract = {
            "task_id": task_id, "objective": "o", "skill": "s", "args": {},
            "expected_output": expected,
        }
        if expected is not None and preds is None:
            preds = expected.get("predicates", [])
        if expected is not None:
            contract["expected_output"] = {"schema": {}, "predicates": preds}
        rec = {
            "record_version": "1.0", "record_type": "task", "task_id": task_id,
            "contract": contract, "outcome": outcome, "failure_kind": kind,
            "exit_code": 0, "output": {}, "predicate_outcomes": [], "rollback": None,
            "tenant_id": "default", "git_head": "deadbeef",
            "recorded_at": "2026-08-10T12:00:00+00:00",
        }
        rec["claim_id"] = _polarity_mapping(rec)[2]
        return rec

    with tempfile.TemporaryDirectory(prefix="task-producer-self-test-") as tmp:
        ledger = Path(tmp)

        # 1. VERIFIED -> SUPPORTING on claim:done:task, ledger VERIFIED
        r1 = produce(record(expected={"predicates": [{"kind": "exit_code", "code": 0}]}), ledger, "deadbeef")
        assert r1["ingested"] is True
        assert r1["artifact"]["polarity"] == POLARITY_SUPPORTING
        assert r1["artifact"]["evidence_type"] == "task_verified"
        assert r1["claim_id"].startswith("claim:done:task:")
        assert r1["verdict"] == "VERIFIED"
        assert set(r1["artifact"]["provenance"]) == {
            "execution", "environment", "input", "verifier", "dependency",
        }

        # 2. idempotent replay -> 0 new
        r2 = produce(record(expected={"predicates": [{"kind": "exit_code", "code": 0}]}), ledger, "deadbeef")
        assert r2["ingested"] is False
        assert r2["evidence_ref"] == r1["evidence_ref"]

        # 3. SUBMITTED (no expected_output) -> INCONCLUSIVE, never elevates
        r3 = produce(record(task_id="t.2", outcome="SUBMITTED"), ledger, "deadbeef")
        assert r3["artifact"]["polarity"] == POLARITY_INCONCLUSIVE
        assert r3["artifact"]["evidence_type"] == "task_submitted"
        assert r3["verdict"] == "UNVERIFIED"

        # 4. FAILED predicates -> CONTRADICTING on claim:done
        r4 = produce(record(task_id="t.3", outcome="FAILED", kind="predicates",
                            expected={"predicates": [{"kind": "file_exists", "path": "x"}]}), ledger, "deadbeef")
        assert r4["artifact"]["polarity"] == POLARITY_CONTRADICTING
        assert r4["artifact"]["evidence_type"] == "task_failed_verify"
        assert r4["claim_id"].startswith("claim:done:task:")
        assert r4["verdict"] == "UNVERIFIED"

        # 5. FAILED crash/budget -> CONTRADICTING on claim:ok:task (availability)
        r5 = produce(record(task_id="t.4", outcome="FAILED", kind="budget"), ledger, "deadbeef")
        assert r5["artifact"]["polarity"] == POLARITY_CONTRADICTING
        assert r5["artifact"]["evidence_type"] == "task_failed"
        assert r5["claim_id"] == claim_ok_task("t.4")
        assert r5["verdict"] == "UNVERIFIED"

        # 6. previously-VERIFIED claim:done later contradicted -> REGRESSED
        same = record(task_id="t.5", outcome="VERIFIED",
                      expected={"predicates": [{"kind": "exit_code", "code": 0}]})
        later = dict(same)
        later.update({
            "recorded_at": "2026-08-10T13:00:00+00:00",
            "outcome": "FAILED", "failure_kind": "predicates",
            "predicate_outcomes": [{"kind": "exit_code", "passed": False}],
        })
        later["claim_id"] = _polarity_mapping(later)[2]
        produce(same, ledger, "deadbeef")
        rb = produce(later, ledger, "deadbeef")
        assert rb["verdict"] == "REGRESSED"

        # 7. malformed record + tampered claim fail loud
        try:
            produce({"record_version": "1.0"}, ledger, "deadbeef")
            raise AssertionError("malformed record must fail loud")
        except ValueError:
            pass
        bad = record(task_id="t.6")
        bad["claim_id"] = "claim:done:task:tampered"
        try:
            produce(bad, ledger, "deadbeef")
            raise AssertionError("tampered claim_id must fail loud")
        except ValueError:
            pass

        # 8. TASK_FAILED event shape is pinned
        ev = {
            "event": "TASK_FAILED", "version": "1.0", "task_id": "t.1",
            "goal": "g", "failed_step": "t.1", "failed_index": 0,
            "error": "budget exceeded", "attempt": 1, "max_attempts": 1,
            "decision": "fail", "dag": [], "revised_dag": None,
            "ts": "2026-08-10T12:00:00+00:00",
        }
        stream = append_task_event(ev, ledger)
        assert stream.name == "task_events.jsonl"
        try:
            append_task_event({"event": "TASK_FAILED"}, ledger)
            raise AssertionError("missing pinned fields must fail loud")
        except ValueError:
            pass

        return 0
