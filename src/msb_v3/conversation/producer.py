"""Conversation → Ledger Evidence Producer — the log hop of the envelope.

Implements docs/conversation-ledger-producer-v1.md. Mirrors the task-failure
producer (sovereign-verification/scripts/replay_feedback_events.py) in
discipline: exact §8 artifact shape, content-addressed idempotency + cursor,
fail-loud, deterministic pre-write evidence_ref. New evidence types:
`conversation` (SUPPORTING / INCONCLUSIVE on claim:ans) and
`conversation_block` (CONTRADICTING on claim:ok:query).

The producer is NOT on the model path: deterministic, zero-spend, stdlib +
msb_v3.conversation.envelope only. Nothing here calls a model.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from msb_v3.conversation.envelope import (
    canonical_json,
    claim_id_for_ans,
    claim_id_for_query,
)

TOOLCHAIN = "msb-v3/conversation-ledger"
TOOL_VERSION = "1.0"
TIER = "T3"  # real execution produced the exchange — EXECUTED-level evidence

POLARITY_SUPPORTING = "SUPPORTING"
POLARITY_INCONCLUSIVE = "INCONCLUSIVE"
POLARITY_CONTRADICTING = "CONTRADICTING"

CLAIM_TYPE_ANSWER = "answer_supported"
CLAIM_TYPE_QUERY = "query_answerable"

# Claims that were supported at some point; fresh CONTRADICTING evidence
# against them means REGRESSED (historical green + current red).
_PREVIOUSLY_VERIFIED = ("VERIFIED", "SUPPORTED", "VALIDATED", "REGRESSED")

_REQUIRED_RECORD_FIELDS = (
    "record_version", "trace_id", "mode", "query", "status",
    "input_guardrail", "output_guardrail", "sources", "answer",
    "latency_ms", "git_head", "recorded_at",
)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sanitize(name: str) -> str:
    return "".join("_" if ch in ":+/\\ " else ch for ch in name)


def _canonical_record_text(record: dict) -> str:
    return canonical_json(record)


def record_identity(record: dict) -> str:
    """Content-addressed dedupe identity (NOT (ts, trace_id)): same-second
    distinct exchanges produce distinct hashes; identical content is a true
    duplicate regardless of when it was written."""
    return _sha256(_canonical_record_text(record))


def _polarity_mapping(record: dict) -> tuple[str, str, str]:
    """(polarity, evidence_type, claim_id) — the §4 mapping, the ONLY path to
    an artifact. No other polarity is writable.

    | envelope outcome                          | polarity      | claim              | type                |
    | answered + SUPPORTING                     | SUPPORTING    | claim:ans          | conversation        |
    | answered + UNSUPPORTED                    | INCONCLUSIVE  | claim:ans          | conversation        |
    | answered + output BLOCKED                 | CONTRADICTING | claim:ok:query     | conversation_block  |
    | blocked (input guardrail)                 | CONTRADICTING | claim:ok:query     | conversation_block  |
    """
    query = str(record.get("query", ""))
    if record.get("status") == "blocked":
        return POLARITY_CONTRADICTING, "conversation_block", claim_id_for_query(query)
    output = record.get("output_guardrail") or {}
    out_verdict = output.get("verdict")
    if out_verdict == "BLOCKED":
        return POLARITY_CONTRADICTING, "conversation_block", claim_id_for_query(query)
    if out_verdict == "SUPPORTING":
        return POLARITY_SUPPORTING, "conversation", claim_id_for_ans(
            query,
            _source_ids(record),
            str((record.get("answer") or {}).get("text", "")),
        )
    # UNSUPPORTED (or anything else answered) → INCONCLUSIVE, never elevates.
    return POLARITY_INCONCLUSIVE, "conversation", claim_id_for_ans(
        query,
        _source_ids(record),
        str((record.get("answer") or {}).get("text", "")),
    )


def _source_ids(record: dict) -> list[str]:
    return [str(s.get("source_id", "")) for s in record.get("sources", []) if s.get("source_id")]


def _validate_record(record: dict) -> None:
    """Fail loud on malformed records — never a silent no-op."""
    if not isinstance(record, dict):
        raise ValueError("conversation record must be a dict")
    missing = [f for f in _REQUIRED_RECORD_FIELDS if f not in record]
    if missing:
        raise ValueError(f"malformed conversation record: missing {', '.join(missing)}")
    if str(record.get("record_version")) != "1.0":
        raise ValueError(f"unsupported record_version: {record.get('record_version')!r}")
    if record.get("status") not in ("answered", "blocked"):
        raise ValueError(f"record status must be 'answered' or 'blocked', got {record.get('status')!r}")
    if not record.get("trace_id") or not record.get("query"):
        raise ValueError("record requires non-empty trace_id and query")


def _self_consistency_check(record: dict) -> str:
    """Recompute the claim_id from the record alone and verify it matches the
    envelope-supplied claim_id (when the envelope carried one). Mismatch ⇒
    fail loud — never a mismatched attachment.

    Returns the claim_id the artifact should carry.

    Note (asymmetry is intentional): only ANSWERED records carry a
    verifiable envelope-supplied claim_id. BLOCKED records attack
    claim:ok:query, which is derived from the record's own query — there is
    no external claim to compare against. That is inherent to the
    record-is-source-of-truth design, not a tamper-detection gap.
    """
    answer = record.get("answer") or {}
    claim_id = _polarity_mapping(record)[2]
    supplied = answer.get("claim_id")
    if supplied and supplied != claim_id:
        raise ValueError(
            f"self-consistency failure: envelope claim_id {supplied!r} != recomputed {claim_id!r}"
        )
    return claim_id


def build_evidence_artifact(record: dict, git_head: str) -> dict[str, Any]:
    """The blueprint §8 canonical evidence object — same top-level keys and
    five provenance layers as the task-failure producer, so the ledger has ONE
    evidence schema. evidence_id is content-addressed on the RECORD (mirrors
    the replay producer hashing its source event — the artifact carries its
    own id, so hashing the artifact would be circular)."""
    # Record-is-source-of-truth pin: git_head derives from the RECORD (it is
    # part of the record hash that evidence_id is content-addressed on). The
    # parameter is only a fallback for records that carry no git_head — the
    # same record with a different param must never produce different bytes.
    git_head = str(record.get("git_head") or git_head)
    record_hash = _sha256(_canonical_record_text(record))
    evidence_id = f"ev_{record_hash[:12]}"
    polarity, ev_type, claim_id = _polarity_mapping(record)
    output = record.get("output_guardrail") or {}
    input_g = record.get("input_guardrail") or {}
    query = str(record.get("query", ""))
    sources = record.get("sources", [])

    return {
        "evidence_id": evidence_id,
        "subject_id": f"trace:{record.get('trace_id', '')}",
        "claim_id": claim_id,
        "evidence_type": ev_type,
        "polarity": polarity,
        "git_head": git_head,
        "artifact_hash": record_hash[:16],
        "toolchain": TOOLCHAIN,
        "timestamp": str(record.get("recorded_at", "")),
        "result": "ANSWERED" if record.get("status") == "answered" else "BLOCKED",
        "provenance": {
            "execution": {
                "mode": record.get("mode"),
                "input_guardrail": input_g.get("verdict"),
                "output_guardrail": output.get("verdict"),
                "citation_rate": output.get("citation_rate"),
                "latency_ms": record.get("latency_ms"),
            },
            "environment": {
                "tenant_id": record.get("tenant_id"),
                "session_id": record.get("session_id"),
                "model": record.get("model"),
                "git_head": git_head,
            },
            "input": {
                "query": query,
                "query_hash": _sha256(canonical_json({"query": query}))[:12],
                "source_ids": sorted(_source_ids(record)),
                "dag": record.get("workflow_dag"),
            },
            "verifier": {
                "tool": TOOLCHAIN,
                "version": TOOL_VERSION,
                "output_guardrail": output.get("verdict"),
            },
            "dependency": {
                "sources": [
                    {
                        "source_id": s.get("source_id"),
                        "score": s.get("score"),
                        "source_ts": s.get("source_ts"),
                        "freshness": s.get("freshness"),
                    }
                    for s in sources
                ],
            },
        },
        "freshness": "FRESH",
    }


def artifact_filename(record: dict, artifact: dict) -> str:
    """Deterministic pre-write path: `<sanitized recorded_at>_<evidence_id>.json`.
    The envelope's evidence_ref is computable BEFORE the producer runs."""
    ts = _sanitize(str(record.get("recorded_at", "")))
    return f"{ts}_{artifact['evidence_id']}.json"


def evidence_ref(record: dict, artifact: dict) -> str:
    return f"ledger://evidence/{artifact['evidence_type']}/{artifact_filename(record, artifact)}"


# --- ledger state (cursor + claim registry) — mirrors the replay producer ---


def _empty_registry() -> dict[str, Any]:
    return {"claims": [], "generated_at": None}


def load_registry(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _empty_registry()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _empty_registry()
    if not isinstance(data, dict) or not isinstance(data.get("claims"), list):
        return _empty_registry()
    return data


def load_cursor(path: Path) -> set:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return set(data.get("processed", []))
    except (json.JSONDecodeError, OSError, AttributeError):
        return set()


def _verdict_for(prior: Optional[dict], polarity: str) -> str:
    """§7 verdict transition table — the governor's rule, applied here.

    - SUPPORTING: elevates (VERIFIED); CONTESTED when contradicting evidence
      already exists (supporting + contradicting coexist).
    - INCONCLUSIVE: never elevates, never regresses — only CONTRADICTING
      attacks.
    - CONTRADICTING: never-supported → UNVERIFIED; previously supported →
      REGRESSED (historical green + current red); contested stays contested.
    """
    prior_verdict = (prior or {}).get("verdict") if prior else None
    if polarity == POLARITY_CONTRADICTING:
        if not prior:
            return "UNVERIFIED"
        if prior_verdict in _PREVIOUSLY_VERIFIED:
            return "REGRESSED"
        if prior_verdict == "CONTESTED":
            return "CONTESTED"
        return "UNVERIFIED"
    if polarity == POLARITY_SUPPORTING:
        if not prior:
            return "VERIFIED"
        if prior_verdict == "CONTESTED":
            return "CONTESTED"
        if prior.get("negative_evidence"):
            return "CONTESTED"
        return "VERIFIED"
    # INCONCLUSIVE — never elevates a claim, but a previously-supported
    # claim:ans later judged UNSUPPORTED is REGRESSED (historical green +
    # current red — §7 row 4 'later unsupported'). Same claim_id means the
    # same answer was once supported and is now not: a real contradiction.
    if not prior:
        return "UNVERIFIED"
    if prior_verdict in _PREVIOUSLY_VERIFIED:
        return "REGRESSED"
    if prior_verdict == "CONTESTED":
        return "CONTESTED"
    return "UNVERIFIED"


def _claim_text(claim_id: str, record: dict) -> str:
    if claim_id.startswith("claim:ans:"):
        return f'answer to query "{record.get("query", "")}" is supported by its sources'
    return f'query "{record.get("query", "")}" is safely answerable'


def _claim_entry(
    claim_id: str, record: dict, artifact: dict, prior: Optional[dict],
) -> dict[str, Any]:
    polarity = artifact["polarity"]
    verdict = _verdict_for(prior, polarity)
    ts = str(record.get("recorded_at", ""))
    ev_id = artifact["evidence_id"]
    entry = {
        "claim_id": claim_id,
        "subject": f"trace:{record.get('trace_id', '')}",
        "text": _claim_text(claim_id, record),
        "claim_type": CLAIM_TYPE_QUERY if claim_id.startswith("claim:ok:") else CLAIM_TYPE_ANSWER,
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


# --- the producer contract ---


def produce(
    record: dict,
    ledger_dir: Path,
    git_head: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """produce(record, ledger_dir, git_head, dry_run=False) -> artifact ref + claims state.

    - Deterministic: same record + same ledger ⇒ same artifact, same claims state.
    - Idempotent: replaying the same record ingests 0 new (cursor).
    - Fail loud: malformed record, self-consistency mismatch, or ledger write
      failure → raises; never a silent no-op.
    - dry_run computes the ref and resulting claims state WITHOUT writing.
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

    processed = load_cursor(cursor_file)
    registry = load_registry(claims_file)
    by_id = {c.get("claim_id"): c for c in registry["claims"]}

    already = record_hash in processed
    if already and not dry_run:
        prior = by_id.get(claim_id)
        entry = _claim_entry(claim_id, record, artifact, prior) if prior else None
        return {
            "evidence_ref": ref,
            "filename": filename,
            "artifact": artifact,
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
        by_id[claim_id] = entry
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
        "evidence_ref": ref,
        "filename": filename,
        "artifact": artifact,
        "claim_id": claim_id,
        "verdict": entry["verdict"],
        "ingested": True,
    }


def append_record(record: dict, ledger_dir: Path) -> Path:
    """Append a conversation record to the durable stream (`<ledger>/records/
    conversation.jsonl`) — the input the offline replay eval and the CLI
    consume. Fail loud on write failure."""
    _validate_record(record)
    records_dir = ledger_dir / "records"
    records_dir.mkdir(parents=True, exist_ok=True)
    stream = records_dir / "conversation.jsonl"
    with stream.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
    return stream


# --- stream ingestion (the CLI core — mirrors replay's ingest) ---


def ingest_stream(
    records_path: Path,
    ledger_dir: Path,
    git_head: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Replay the conversation record stream into the ledger (idempotent).

    An ABSENT stream is not an incident (nothing logged yet) — status
    "no_records", exit 0 upstream. A MALFORMED stream is an incident —
    status "error", and nothing is ingested (a torn write must never
    silently erase evidence).
    """
    if not records_path.exists():
        return {
            "status": "no_records", "records": 0, "ingested": 0, "skipped": 0,
            "errors": [], "claims": 0,
        }
    records: list[dict] = []
    errors: list[str] = []
    for i, line in enumerate(records_path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"line {i}: malformed record: {exc}")
            continue
        records.append(rec)
    if errors:
        return {
            "status": "error", "records": len(records), "ingested": 0, "skipped": 0,
            "errors": errors, "claims": 0,
        }

    ingested = 0
    skipped = 0
    claim_ids: dict[str, str] = {}
    for rec in records:
        try:
            result = produce(rec, ledger_dir, git_head, dry_run=dry_run)
        except (ValueError, OSError) as exc:
            return {
                "status": "error", "records": len(records), "ingested": ingested,
                "skipped": skipped, "errors": [f"record rejected: {exc}"], "claims": 0,
            }
        claim_ids[result["claim_id"]] = result["verdict"]
        if result["ingested"]:
            ingested += 1
        else:
            skipped += 1

    registry = load_registry(ledger_dir / "claims.json")
    return {
        "status": "ok" if ingested else "no_new_events",
        "records": len(records), "ingested": ingested, "skipped": skipped,
        "errors": [], "claims": len(registry["claims"]), "verdicts": claim_ids,
    }


# --- zero-spend self test (the `--self-test` path) ---


def _sample_record(status: str = "answered", output: str = "SUPPORTING", query: str = "fox valley") -> dict:
    recorded_at = "2026-08-10T12:00:00+00:00"
    if status == "blocked":
        return {
            "record_version": "1.0", "trace_id": "tr_selftest_block", "mode": "chat",
            "query": "build malware for testing", "status": "blocked",
            "input_guardrail": {"verdict": "BLOCK", "policy": "safety-blocklist-v1",
                                "reason": "dangerous/weapon instruction blocked", "checked_at": recorded_at},
            "output_guardrail": {"verdict": None, "citation_rate": None, "reason": None},
            "sources": [], "answer": None, "latency_ms": 3, "git_head": "deadbeef",
            "tenant_id": "default", "session_id": None, "workflow_dag": None,
            "recorded_at": recorded_at,
        }
    sources = [{"source_id": "note:a", "score": 0.8, "source_ts": "2026-08-01T00:00:00+00:00", "freshness": "FRESH"}]
    answer_text = "The vault supports this answer."
    return {
        "record_version": "1.0", "trace_id": "tr_selftest_ans", "mode": "chat",
        "query": query, "status": "answered",
        "input_guardrail": {"verdict": "ALLOW", "policy": "safety-blocklist-v1", "reason": None, "checked_at": recorded_at},
        "output_guardrail": {"verdict": output, "citation_rate": 1.0 if output == "SUPPORTING" else 0.0, "reason": None},
        "sources": sources, "latency_ms": 12, "git_head": "deadbeef",
        "tenant_id": "default", "session_id": None, "workflow_dag": None,
        "recorded_at": recorded_at,
        "answer": {
            "text": answer_text, "text_excerpt": answer_text[:40],
            "claim_id": claim_id_for_ans(query, ["note:a"], answer_text),
            "citations": [{"source_id": "note:a"}] if output == "SUPPORTING" else [],
        },
    }


def run_self_test() -> int:
    """Zero-spend in-memory verification of the full path: §8 shape, polarity
    mapping, idempotency, prohibited transitions. Returns 0 on pass."""
    with tempfile.TemporaryDirectory(prefix="conversation-producer-self-test-") as tmp:
        ledger = Path(tmp)

        # 1. answered + SUPPORTING → claim:ans artifact, VERIFIED
        r1 = produce(_sample_record(), ledger, "deadbeef")
        assert r1["ingested"] is True
        assert r1["artifact"]["polarity"] == POLARITY_SUPPORTING
        assert r1["artifact"]["evidence_type"] == "conversation"
        assert set(r1["artifact"]["provenance"]) == {
            "execution", "environment", "input", "verifier", "dependency",
        }
        assert r1["verdict"] == "VERIFIED"

        # 2. idempotent replay → 0 new
        r2 = produce(_sample_record(), ledger, "deadbeef")
        assert r2["ingested"] is False
        assert r2["evidence_ref"] == r1["evidence_ref"]

        # 3. block → claim:ok:query CONTRADICTING, UNVERIFIED (never supported)
        r3 = produce(_sample_record(status="blocked"), ledger, "deadbeef")
        assert r3["artifact"]["polarity"] == POLARITY_CONTRADICTING
        assert r3["artifact"]["evidence_type"] == "conversation_block"
        assert r3["claim_id"] == claim_id_for_query("build malware for testing")
        assert r3["verdict"] == "UNVERIFIED"

        # 4. previously-VERIFIED claim:ans later judged UNSUPPORTED (same
        #    query+sources+answer → same claim_id) → REGRESSED: historical
        #    green + current red (the reachable §7 row-4 path — CONTRADICTING
        #    only ever targets claim:ok:query, which is never pre-supported).
        query = "fox valley"
        support = {
            "record_version": "1.0", "trace_id": "tr_x", "mode": "chat", "query": query,
            "status": "answered",
            "input_guardrail": {"verdict": "ALLOW", "policy": "safety-blocklist-v1", "reason": None, "checked_at": "2026-08-10T12:00:00+00:00"},
            "output_guardrail": {"verdict": "SUPPORTING", "citation_rate": 1.0, "reason": None},
            "sources": [{"source_id": "note:a", "score": 0.8, "source_ts": "2026-08-01T00:00:00+00:00", "freshness": "FRESH"}],
            "answer": {
                "text": "supported text", "text_excerpt": "supported text",
                "claim_id": claim_id_for_ans(query, ["note:a"], "supported text"),
                "citations": [{"source_id": "note:a"}],
            },
            "latency_ms": 1, "git_head": "deadbeef", "tenant_id": "default",
            "session_id": None, "workflow_dag": None, "recorded_at": "2026-08-10T12:00:00+00:00",
        }
        # Same content, later judged UNSUPPORTED → same claim_id, INCONCLUSIVE.
        later_unsupported = dict(support)
        later_unsupported.update({
            "trace_id": "tr_y",
            "output_guardrail": {"verdict": "UNSUPPORTED", "citation_rate": 0.0, "reason": None},
            "recorded_at": "2026-08-10T13:00:00+00:00",
        })
        later_unsupported["answer"] = dict(support["answer"])
        later_unsupported["answer"]["citations"] = []
        produce(support, ledger, "deadbeef")
        rb = produce(later_unsupported, ledger, "deadbeef")
        assert rb["claim_id"] == claim_id_for_ans(query, ["note:a"], "supported text")
        assert rb["artifact"]["polarity"] == POLARITY_INCONCLUSIVE
        assert rb["verdict"] == "REGRESSED"

        # 5. INCONCLUSIVE never elevates: UNSUPPORTED answer on a NEVER-verified
        #    claim (different query → different claim:ans) → UNVERIFIED
        r5 = produce(_sample_record(output="UNSUPPORTED", query="a never-supported query"), ledger, "deadbeef")
        assert r5["artifact"]["polarity"] == POLARITY_INCONCLUSIVE
        assert r5["verdict"] == "UNVERIFIED"

        # 6. dry-run writes nothing
        ledger2 = Path(tmp) / "dry"
        r6 = produce(_sample_record(), ledger2, "deadbeef", dry_run=True)
        assert r6["ingested"] is True  # computed as if new
        assert not (ledger2 / "evidence").exists()

        # 7. self-consistency mismatch fails loud
        bad = _sample_record()
        bad["answer"]["claim_id"] = "claim:ans:tampered"
        try:
            produce(bad, ledger, "deadbeef")
            raise AssertionError("tampered claim_id must fail loud")
        except ValueError:
            pass

        # 8. malformed record fails loud
        try:
            produce({"record_version": "1.0"}, ledger, "deadbeef")
            raise AssertionError("malformed record must fail loud")
        except ValueError:
            pass

        return 0


# --- ledger location (open decision 1: shared constellation ledger) ---


def default_ledger_dir() -> Path:
    env = os.getenv("MSB_CONVERSATION_LEDGER_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".hermes" / "skills" / "sovereign-verification" / "ledger"


def default_git_head(repo_root: Optional[Path] = None) -> str:
    env = os.getenv("MSB_CONVERSATION_GIT_HEAD")
    if env:
        return env
    import subprocess
    root = repo_root or Path(__file__).resolve().parents[3]
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return "unknown"
