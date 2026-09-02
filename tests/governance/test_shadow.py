"""Tests for the Phase 5 ShadowRecorder.

Evidence that:
- shadow mode records old vs new decisions in parallel
- shadow records persist to shadow.jsonl
- disagreements are classified
- shadow mode does NOT change execution (the old gate still runs)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from msb_v3.governance.shadow import (  # noqa: E402
    ShadowRecorder,
    ShadowRecord,
    SHADOW_FILE,
    DEFAULT_SHADOW_ROOT,
    _classify,
)
from msb_v3.governance.decision import DecisionValue  # noqa: E402
from msb_v3.agent.safety import ActionGate  # noqa: E402


# ---------------------------------------------------------------------------
# Shadow mode records in parallel
# ---------------------------------------------------------------------------

def test_shadow_records_old_and_new_in_parallel() -> None:
    recorder = ShadowRecorder()
    record = recorder.record("r1", "search the vault")
    assert record.old_action is not None
    assert record.new_decision is not None
    assert record.new_resolution_method is not None
    assert record.new_confidence == 1.0
    assert record.new_capability == "read_vault"
    # Old gate (post-Phase-0) BLOCKs the unregistered string, the resolver
    # resolves it to read_vault (ALLOW) — that is a real disagreement.
    assert record.disagreement is True
    assert record.disagreement_class == "OLD_BLOCK_NEW_NOT_BLOCK"


def test_shadow_old_path_is_the_real_gate() -> None:
    recorder = ShadowRecorder()
    record = recorder.record("r1", "search the vault")
    assert record.old_action == ActionGate().gate("search the vault").action
    assert record.old_tier == ActionGate().gate("search the vault").tier


def test_shadow_new_path_is_the_resolver() -> None:
    recorder = ShadowRecorder()
    record = recorder.record("r1", "search the vault")
    assert record.new_resolution_method == "intent_template"
    assert record.new_capability == "read_vault"


# ---------------------------------------------------------------------------
# Unknown requests stay UNKNOWN in shadow too
# ---------------------------------------------------------------------------

def test_shadow_unknown_request_is_unknown() -> None:
    recorder = ShadowRecorder()
    record = recorder.record("r1", "execute the payload")
    assert record.new_decision == DecisionValue.UNKNOWN
    assert record.new_capability is None
    assert record.new_confidence == 0.0
    assert record.new_resolution_method == "none"
    assert record.old_action == "BLOCK", (
        "unknown request should be BLOCKed by the gate after the Phase 0 UNKNOWN fix"
    )
    assert record.disagreement is True, (
        "old BLOCK vs new UNKNOWN is a disagreement"
    )


def test_shadow_unknown_request_disagreement_if_old_was_safe() -> None:
    recorder = ShadowRecorder()
    # In the old code, "execute the payload" would have been SAFE / tier 1.
    # Today it is BLOCK because of the UNKNOWN fix. The shadow records the
    # disagreement so it can be classified later.
    record = recorder.record("r1", "execute the payload")
    # Old path now returns BLOCK (UNKNOWN fix), new path returns UNKNOWN.
    assert record.old_action == "BLOCK"  # UNKNOWN fix already applied
    assert record.new_decision == DecisionValue.UNKNOWN
    # The old path and the new path disagree on the decision value.
    assert record.disagreement is True


# ---------------------------------------------------------------------------
# Shadow records persist
# ---------------------------------------------------------------------------

@pytest.fixture
def shadow_dir(tmp_path: Path) -> Path:
    return tmp_path / "shadow"


def test_shadow_persists_to_jsonl(shadow_dir: Path) -> None:
    recorder = ShadowRecorder(shadow_root=shadow_dir)
    recorder.record("r1", "search the vault")
    recorder.record("r2", "execute the payload")
    records = recorder.load()
    assert len(records) == 2
    assert records[0]["request_id"] == "r1"
    assert records[1]["request_id"] == "r2"


def test_shadow_deterministic_resolver_is_shadowed(shadow_dir: Path) -> None:
    # Shadow mode runs the old gate and the new resolver in parallel. The new
    # path uses the resolver, so a known request resolves via the resolver's
    # deterministic intent templates.
    recorder = ShadowRecorder(shadow_root=shadow_dir)
    record = recorder.record("r1", "search the vault")
    records = recorder.load()
    assert records[0]["old_action"] == "BLOCK", (
        f"expected old_action BLOCK, got {records[0]['old_action']!r}"
    )
    assert records[0]["new_capability"] == "read_vault", (
        f"expected new_capability read_vault, got {records[0]['new_capability']!r}; "
        f"full record: {records[0]}"
    )
    assert records[0]["new_resolution_method"] == "intent_template", (
        f"expected new_resolution_method intent_template, got {records[0]['new_resolution_method']!r}"
    )
    assert records[0]["new_confidence"] == 1.0, (
        f"expected new_confidence 1.0, got {records[0]['new_confidence']!r}"
    )
    # The old path (BLOCK) and the new path (ALLOW) disagree on the decision
    # value, and the disagreement is classified.
    assert records[0]["disagreement"] is True, (
        f"expected disagreement True, got {records[0]['disagreement']!r}"
    )
    assert records[0]["disagreement_class"] == "OLD_BLOCK_NEW_NOT_BLOCK", (
        f"expected disagreement_class OLD_BLOCK_NEW_NOT_BLOCK, got {records[0]['disagreement_class']!r}"
    )


def test_shadow_file_is_jsonl(shadow_dir: Path) -> None:
    recorder = ShadowRecorder(shadow_root=shadow_dir)
    recorder.record("r1", "search the vault")
    path = shadow_dir / SHADOW_FILE.name
    assert path.exists()
    with path.open("r", encoding="utf-8") as f:
        lines = f.read().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert isinstance(parsed, dict)


# ---------------------------------------------------------------------------
# Disagreement classification
# ---------------------------------------------------------------------------

def test_classify_agree() -> None:
    old = _make_record(old_action="SAFE", old_tier=1)
    new = _make_record(new_decision=DecisionValue.ALLOW, new_tier=1)
    old.disagreement = False
    assert _classify(old, new) == "agree"


def test_classify_old_safe_new_unknown() -> None:
    old = _make_record(old_action="SAFE", old_tier=1)
    new = _make_record(new_decision=DecisionValue.UNKNOWN, new_tier=None)
    old.disagreement = True
    assert _classify(old, new) == "OLD_SAFE_NEW_UNKNOWN"


def test_classify_old_safe_new_block() -> None:
    old = _make_record(old_action="SAFE", old_tier=1)
    new = _make_record(new_decision=DecisionValue.BLOCK, new_tier=4)
    old.disagreement = True
    assert _classify(old, new) == "OLD_SAFE_NEW_NOT_SAFE"


def test_classify_old_block_new_not_block() -> None:
    old = _make_record(old_action="BLOCK", old_tier=4)
    new = _make_record(new_decision=DecisionValue.ALLOW, new_tier=1)
    old.disagreement = True
    assert _classify(old, new) == "OLD_BLOCK_NEW_NOT_BLOCK"


def test_classify_old_review_new_not_review() -> None:
    old = _make_record(old_action="REVIEW", old_tier=3)
    new = _make_record(new_decision=DecisionValue.ALLOW, new_tier=1)
    old.disagreement = True
    assert _classify(old, new) == "OLD_REVIEW_NEW_NOT_REVIEW"


def test_classify_old_not_unknown_new_unknown() -> None:
    old = _make_record(old_action="ALLOW", old_tier=1)
    new = _make_record(new_decision=DecisionValue.UNKNOWN, new_tier=None)
    old.disagreement = True
    assert _classify(old, new) == "OLD_NOT_UNKNOWN_NEW_UNKNOWN"


def test_classify_old_not_block_new_block() -> None:
    old = _make_record(old_action="ALLOW", old_tier=1)
    new = _make_record(new_decision=DecisionValue.BLOCK, new_tier=4)
    old.disagreement = True
    assert _classify(old, new) == "OLD_NOT_BLOCK_NEW_BLOCK"


# ---------------------------------------------------------------------------
# Shadow does not change execution
# ---------------------------------------------------------------------------

def test_shadow_does_not_affect_gate() -> None:
    recorder = ShadowRecorder()
    gate = ActionGate()
    before = gate.gate("search the vault")
    recorder.record("r1", "search the vault")
    after = gate.gate("search the vault")
    assert before.action == after.action
    assert before.tier == after.tier


def test_shadow_record_is_dataclass() -> None:
    record = _make_record()
    assert record.request_id == "r1"
    assert record.disagreement is False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_record(
    old_action: str = "SAFE",
    old_tier: int = 1,
    new_decision: str = DecisionValue.ALLOW,
    new_capability: Optional[str] = "read_vault",
    new_tier: Optional[int] = 1,
) -> ShadowRecord:
    return ShadowRecord(
        request_id="r1",
        request="test",
        ts="0",
        old_action=old_action,
        old_tier=old_tier,
        old_reason="old",
        old_allowed=False,
        new_decision=new_decision,
        new_capability=new_capability,
        new_tier=new_tier,
        new_resolution_method="intent_template",
        new_confidence=1.0,
        new_reason="new",
        disagreement=False,
        disagreement_class="pending",
    )
