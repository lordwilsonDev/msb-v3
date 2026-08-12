"""Tests for the context builder (msb_v3.fabric.context)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from msb_v3.fabric.context import ContextBuilder, _tokens  # noqa: E402


def _match(i: int, score: float, text: str, source: str = "") -> dict:
    return {"id": f"id-{i}", "score": score, "text": text, "source": source or f"s{i}.md"}


def test_budget_never_exceeded_with_large_sources() -> None:
    builder = ContextBuilder(budget_tokens=200)
    matches = [_match(i, 1.0 - i * 0.01, "x" * 400) for i in range(10)]
    context = builder.build("q", matches, system="sys")
    assert context.tokens <= 200
    assert context.ledger.evicted_matches >= 1  # the overflow items were dropped
    assert context.ledger.included_matches >= 1


def test_high_score_matches_survive_eviction() -> None:
    # Each match costs ~35 tokens (100-char body + label); budget 80 fits two
    # and forces the weakest out — proving score-ordered eviction.
    builder = ContextBuilder(budget_tokens=80)
    matches = [
        _match(1, 0.99, "best hit " + "a" * 100),
        _match(2, 0.10, "weak hit " + "b" * 100),
        _match(3, 0.20, "mid hit " + "c" * 100),
    ]
    context = builder.build("q", matches)
    assert context.tokens <= 80
    included = [i["source"] for i in context.ledger.items]
    assert "s1.md" in included  # top score kept
    assert "s2.md" not in included  # weakest evicted first


def test_matches_without_text_are_skipped() -> None:
    builder = ContextBuilder(budget_tokens=500)
    matches = [_match(1, 0.9, "real text"), {"id": "empty", "score": 0.9, "text": "   "}]
    context = builder.build("q", matches)
    assert context.ledger.included_matches == 1


def test_system_and_query_always_present() -> None:
    builder = ContextBuilder(budget_tokens=1000)
    context = builder.build("the query", [], system="the system prompt")
    assert "the system prompt" in context.text
    assert "the query" in context.text
    assert context.ledger.included_matches == 0


def test_truncation_when_even_system_and_query_overflow() -> None:
    builder = ContextBuilder(budget_tokens=60)
    context = builder.build("x" * 500, [], system="s" * 500)
    assert context.tokens <= 60
    assert context.ledger.truncated is True


def test_domain_declared_is_annotated() -> None:
    builder = ContextBuilder(budget_tokens=1000)
    context = builder.build("q", [], declare_domain="episodic")
    assert "Domain: episodic" in context.text


def test_budget_invariant_holds_at_boundary_with_headers() -> None:
    """The assembled text (including the "Sources:" header and separators)
    must never exceed the budget — even when an item exactly fills the
    per-item allowance, the header pushes the total over and must trigger
    eviction."""
    builder = ContextBuilder(budget_tokens=100)
    # A single body long enough that body+header+query would exceed 100.
    matches = [_match(1, 0.99, "x" * 320)]  # 80 tokens + overhead
    context = builder.build("q", matches, declare_domain="knowledge")
    assert context.tokens <= 100
    # Either the item was evicted or it fit; either way the invariant holds.
    assert context.ledger.total_tokens <= context.ledger.budget_tokens


def test_eviction_happens_at_the_assembly_level() -> None:
    # Two items whose bodies fit the per-item allowance separately, but whose
    # combined assembly (headers + separators) exceeds the budget — the
    # rebuild loop must drop the lowest-score item, not overshoot.
    builder = ContextBuilder(budget_tokens=120)
    matches = [_match(1, 0.9, "a" * 300), _match(2, 0.8, "b" * 300)]
    context = builder.build("q", matches)
    assert context.tokens <= 120
    # 300-char bodies (~75 tokens each) both fit the per-item allowance but
    # their combined assembly exceeds 120 — the rebuild must drop one.
    assert context.ledger.included_matches == 1


def test_deterministic_given_same_input() -> None:
    builder = ContextBuilder(budget_tokens=400)
    matches = [_match(i, 0.9 - i * 0.05, "text" * 30) for i in range(5)]
    a = builder.build("q", matches, system="s")
    b = builder.build("q", matches, system="s")
    assert a.text == b.text
    assert a.ledger.items == b.ledger.items


def test_zero_budget_rejected() -> None:
    with pytest.raises(ValueError):
        ContextBuilder(budget_tokens=0)


def test_tokens_estimate_is_positive_and_deterministic() -> None:
    assert _tokens("") == 1
    assert _tokens("hello world") == 2  # 11 chars // 4
    assert _tokens("a") == 1
