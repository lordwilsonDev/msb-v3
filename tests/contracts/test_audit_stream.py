'''Structured audit stream — the runtime wiring that persists one evidence
receipt per governed run and keeps the Prometheus scrape reconciled with it.

This suite pins the *emission* side (the composition function itself is
pinned in test_evidence_receipt.py): every handle() cycle emits exactly one
JSON line to logs/audit.jsonl, denied and errored runs are reconstructable,
a log-write failure is fail-open (never breaks the run), and the model-call
/ MoIE-verdict counters move in lockstep with the emitted line.
'''

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _helpers import (  # noqa: E402
    INTENT_WITH_WRITE,
    Audit,
    FakeMoIE,
    FakeProvider,
    SequenceClient,
)

from msb_v3.agent.handle import handle  # noqa: E402
from msb_v3.agent.safety import ActionGate  # noqa: E402
from msb_v3.core.config import settings  # noqa: E402
from msb_v3.observability.metrics import MODEL_CALLS, MOIE_VERDICTS  # noqa: E402


def _redirect_audit_log(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    """Point the emitter at a temp file. audit_log_path() reads the settings
    attribute on every call, so patching the attribute (not the env, which is
    read once at Settings() instantiation) redirects it cleanly."""
    monkeypatch.setattr(settings, "audit_log_path", str(path))


def _lines(path: Path) -> list[dict]:
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


@pytest.mark.asyncio
async def test_every_cycle_emits_exactly_one_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    '''A full PASS run emits exactly one JSON line, and the line is the same
    receipt the composition function produces (request_id == run_id).'''
    log = tmp_path / "audit.jsonl"
    _redirect_audit_log(monkeypatch, log)

    result = await handle(
        "research the vault and write a client brief",
        client=SequenceClient(INTENT_WITH_WRITE),
        approve=True,
        provider=FakeProvider(tmp_path),
        gate=ActionGate(audit_chain=Audit()),
        moie=FakeMoIE("APPROVE"),
        spine=None,
    )
    assert result.verdict == "PASS"

    receipts = _lines(log)
    assert len(receipts) == 1
    assert receipts[0]["request_id"] == result.run_id
    assert receipts[0]["execution_result"]["verdict"] == "PASS"


@pytest.mark.asyncio
async def test_denied_and_errored_runs_also_emit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    '''A quick-reject BLOCK (denied) and an empty-request ERROR each emit a
    receipt — denied/errored runs are reconstructable from the stream too.'''
    log = tmp_path / "audit.jsonl"
    _redirect_audit_log(monkeypatch, log)

    denied = await handle(
        "rm -rf production",
        client=SequenceClient(INTENT_WITH_WRITE),
        approve=True,
        provider=FakeProvider(tmp_path),
        gate=ActionGate(audit_chain=Audit()),
        moie=FakeMoIE("BLOCK"),
        spine=None,
    )
    errored = await handle("")

    receipts = _lines(log)
    assert len(receipts) == 2
    by_id = {r["request_id"]: r for r in receipts}

    assert denied.verdict == "BLOCKED"
    assert by_id[denied.run_id]["authorization_decision"] == "DENY"
    assert by_id[denied.run_id]["model_calls"] == 0

    assert errored.verdict == "ERROR"
    assert by_id[""]["execution_result"]["verdict"] == "ERROR"


@pytest.mark.asyncio
async def test_log_write_failure_is_fail_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    '''A log path whose parent is a regular file makes the append fail; the
    run must still complete (observability degrades, the run never does).'''
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("a file, not a directory")
    monkeypatch.setattr(settings, "audit_log_path", str(blocker / "audit.jsonl"))

    result = await handle(
        "rm -rf production",
        client=SequenceClient(INTENT_WITH_WRITE),
        approve=True,
        provider=FakeProvider(tmp_path),
        gate=ActionGate(audit_chain=Audit()),
        moie=FakeMoIE("BLOCK"),
        spine=None,
    )
    assert result.verdict == "BLOCKED"  # the run is unaffected


@pytest.mark.asyncio
async def test_metrics_move_in_lockstep_with_emitted_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    '''The model-call and MoIE-verdict counters increment by exactly what the
    emitted line records, so the scrape and the log cannot diverge.'''
    log = tmp_path / "audit.jsonl"
    _redirect_audit_log(monkeypatch, log)

    calls_before = MODEL_CALLS.labels(harness="handle")._value.get()
    approve_before = MOIE_VERDICTS.labels(verdict="APPROVE")._value.get()

    result = await handle(
        "research the vault and write a client brief",
        client=SequenceClient(INTENT_WITH_WRITE),
        approve=True,
        provider=FakeProvider(tmp_path),
        gate=ActionGate(audit_chain=Audit()),
        moie=FakeMoIE("APPROVE"),
        spine=None,
    )
    receipt = _lines(log)[0]

    calls_after = MODEL_CALLS.labels(harness="handle")._value.get()
    approve_after = MOIE_VERDICTS.labels(verdict="APPROVE")._value.get()

    assert result.verdict == "PASS"
    assert receipt["moie_verdict"] == "APPROVE"
    assert receipt["model_calls"] >= 2  # intent + plan
    # MODEL_CALLS accumulates the per-run count; MOIE_VERDICTS counts one verdict.
    assert calls_after - calls_before == receipt["model_calls"]
    assert approve_after - approve_before == 1
