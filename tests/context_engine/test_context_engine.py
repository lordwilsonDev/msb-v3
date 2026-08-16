"""Context Engine semantics — layered composition, budgets, and the G3 gate.

Retrievers are injected everywhere so the tests pin the *composition*
contract (priority order, per-layer caps, hard total, required layers,
honest ledger) without touching real seams.
"""

import pytest

from msb_v3.fabric.context_engine import ContextEngine, ContextPackage


def _big(n_chars: int, prefix: str = "x") -> str:
    return (prefix * 37 + " ") * (n_chars // 38) + prefix


def _engine(**overrides):
    return ContextEngine(retrievers=overrides)


def test_compose_includes_required_layers():
    eng = _engine(L0="SYSTEM-INVARIANTS", L1="THE TASK")
    pkg = eng.compose("do the thing")
    assert "SYSTEM-INVARIANTS" in pkg.text
    assert "THE TASK" in pkg.text
    ids = {layer.layer_id for layer in pkg.layers}
    assert {"L0", "L1"} <= ids


def test_optional_layers_in_priority_order():
    eng = _engine(
        L0="s", L1="t", L2="repo-structure", L3="code", L4="memories",
        L5="skills", L6="history", L7="research",
    )
    pkg = eng.compose("task", budget_tokens=20000)
    # with a huge budget, everything fits
    assert pkg.total_tokens < pkg.budget_tokens
    included = [layer.layer_id for layer in pkg.layers if layer.included_tokens > 0]
    assert included == ["L0", "L1", "L2", "L3", "L4", "L5", "L6", "L7"]


def test_hard_total_budget_evicts_bottom_up():
    eng = _engine(
        L0="system", L1="task-text",
        L2=_big(4000), L3=_big(4000), L4=_big(4000), L5=_big(4000),
        L6=_big(4000), L7=_big(4000),
    )
    pkg = eng.compose("t", budget_tokens=500)
    assert pkg.total_tokens <= pkg.budget_tokens  # hard invariant
    evicted = {layer.layer_id for layer in pkg.layers if layer.evicted}
    # optional layers evicted bottom-up (L7 first)
    assert "L7" in evicted
    assert "L2" not in evicted  # highest-priority optional survives
    assert "L0" in {layer.layer_id for layer in pkg.layers if layer.included_tokens > 0}


def test_per_layer_cap_truncates():
    # ~5000 tokens of content vs a per-layer cap of ~1666 -> must truncate
    eng = _engine(L0="s", L1="t", L4=_big(20000))
    pkg = eng.compose("t", budget_tokens=10000)
    l4 = next(layer for layer in pkg.layers if layer.layer_id == "L4")
    assert l4.requested_tokens > 10000 // 6  # content is bigger than the cap
    assert l4.included_tokens <= 10000 // 6 + 1  # capped ≈ budget/6
    assert "truncated" in l4.reason


def test_required_layers_always_present_even_tight():
    eng = _engine(L0=_big(3000), L1=_big(3000), L4=_big(3000))
    pkg = eng.compose("t", budget_tokens=300)
    assert pkg.total_tokens <= pkg.budget_tokens
    ids = {layer.layer_id for layer in pkg.layers if layer.included_tokens > 0}
    assert {"L0", "L1"} <= ids  # both fit, however squeezed


def test_disabled_layer_is_honest():
    eng = _engine(L0="s", L1="t", L7=None)
    pkg = eng.compose("t")
    l7 = next(layer for layer in pkg.layers if layer.layer_id == "L7")
    assert l7.included_tokens == 0
    assert l7.reason == "disabled"


def test_failed_retriever_is_honest():
    def boom():
        raise RuntimeError("qdrant down")

    eng = _engine(L0="s", L1="t", L4=boom)
    pkg = eng.compose("t")
    l4 = next(layer for layer in pkg.layers if layer.layer_id == "L4")
    assert l4.included_tokens == 0
    assert "RuntimeError" in l4.reason


def test_naive_baseline_and_reduction(gate):
    """G3 gate: composition must cut tokens by ≥50% vs the naive baseline."""
    pkg = gate
    assert pkg.naive_tokens > 0
    assert pkg.reduction_pct >= 50.0, f"G3 gate failed: only {pkg.reduction_pct}% reduction"


@pytest.fixture()
def gate() -> ContextPackage:
    eng = _engine(
        L0="system invariants line", L1="task description line",
        L2=_big(3000), L3=_big(3000), L4=_big(3000), L5=_big(3000),
        L6=_big(3000), L7=_big(3000),
    )
    return eng.compose("t", budget_tokens=1200)


def test_empty_task_still_composes():
    eng = _engine(L0="s", L1="")
    pkg = eng.compose("")  # task text empty but L1 renders "Task: "
    assert pkg.text


def test_default_retrievers_never_raise():
    """The built-in retrievers (real seams) must degrade, not explode."""
    eng = ContextEngine()
    pkg = eng.compose("check codegraph context", repo="definitely-not-indexed", tenant="t")
    # no exception; L2/L3 honestly report the repo isn't indexed
    l2 = next(layer for layer in pkg.layers if layer.layer_id == "L2")
    assert "not indexed" in l2.reason or "no symbols" in l2.reason or l2.included_tokens == 0
