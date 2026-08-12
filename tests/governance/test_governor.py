"""OuroborosGovernor unit tests — convergence enforced, not requested."""

from __future__ import annotations

from msb_v3.governance.governor import OuroborosGovernor


def test_continuous_novelty_continues(tmp_path) -> None:
    g = OuroborosGovernor(db_path=str(tmp_path / "g.db"))
    verdict = None
    for i in range(10):
        verdict = g.advise(f"p{i}", novelty=0.8, duplicate_ratio=0.05)
    assert verdict is not None
    assert verdict.action == "CONTINUE"


def test_stall_halts(tmp_path) -> None:
    g = OuroborosGovernor(db_path=str(tmp_path / "g.db"), stall_limit=4, novelty_min=0.05)
    for i in range(3):
        assert g.advise(f"p{i}", novelty=0.01).action == "CONTINUE"
    v = g.advise("p3", novelty=0.01)
    assert v.action == "HALT"
    assert "stall" in v.reason
    assert v.metrics["stall_count"] == 4


def test_duplicate_ratio_halts(tmp_path) -> None:
    g = OuroborosGovernor(db_path=str(tmp_path / "g.db"), dup_ratio_halt=0.5)
    v = g.advise("p1", novelty=0.8, duplicate_ratio=0.9)
    assert v.action == "HALT"
    assert v.metrics["dup_ratio"] == 0.9
    assert v.trim_candidates == []


def test_trim_candidates_suggested(tmp_path) -> None:
    g = OuroborosGovernor(db_path=str(tmp_path / "g.db"), dup_ratio_halt=0.5, history=10)
    g.advise("seed", novelty=0.8, duplicate_ratio=0.9)  # itself a repeat
    v = g.advise("copy", novelty=0.8, duplicate_ratio=0.9)
    assert v.trim_candidates == ["seed"]


def test_declining_trend_slows(tmp_path) -> None:
    g = OuroborosGovernor(db_path=str(tmp_path / "g.db"))
    v = None
    for i, nv in enumerate((0.9, 0.85, 0.8, 0.9, 0.7, 0.6)):
        v = g.advise(f"p{i}", novelty=nv, duplicate_ratio=0.0)
    assert v is not None
    assert v.action == "SLOW"
    assert v.metrics["trend"] == "declining"


def test_history_bounded(tmp_path) -> None:
    g = OuroborosGovernor(db_path=str(tmp_path / "g.db"), history=10)
    for i in range(40):
        g.advise(f"p{i}", novelty=0.5)
    assert len(g.history()) <= 10


def test_fail_closed_halts_on_unreadable_db(tmp_path) -> None:
    db = tmp_path / "g.db"
    g = OuroborosGovernor(db_path=str(db))
    g.advise("warm", novelty=0.8)
    db.unlink()
    db.mkdir()  # db_path is now a directory -> connect fails
    v = g.advise("p2", novelty=0.8)
    assert v.action == "HALT"
    assert v.metrics.get("fail_closed") is True
