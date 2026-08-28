"""META-1C: Probability engine tests — routing matrix + historical performance.

Tests verify:
  - RoutingMatrix seed/query with Laplace smoothing
  - RoutingMatrix observation recording
  - RoutingMatrix worker stats + task type stats
  - RoutingMatrix persistence (save/load roundtrip)
  - HistoricalPerformance recording + worker stats
  - HistoricalPerformance task type stats + recent
  - HistoricalPerformance persistence roundtrip
"""

from __future__ import annotations

from pathlib import Path

from msb_v3.meta.probability.historical_performance import (
    HistoricalPerformance,
    PerformanceEntry,
)
from msb_v3.meta.probability.routing_matrix import RoutingMatrix, RoutingObservation

# ---------------------------------------------------------------------------
# RoutingMatrix
# ---------------------------------------------------------------------------

class TestRoutingMatrix:
    def test_unseeded_returns_laplace_prior(self) -> None:
        m = RoutingMatrix()
        # Laplace prior with alpha=1: (0+1)/(0+2) = 0.5
        assert m.get_probability("unknown", "unknown") == 0.5

    def test_seed_sets_probability(self) -> None:
        m = RoutingMatrix()
        m.seed("qwen3b", "implementation", 0.78)
        prob = m.get_probability("qwen3b", "implementation")
        # seed(0.78, total=10) → 7/10. Laplace: (7+1)/(10+2) = 0.667
        assert 0.5 < prob < 0.85

    def test_successful_observation_increases_probability(self) -> None:
        m = RoutingMatrix()
        m.seed("qwen3b", "coding", 0.5, total=10)
        before = m.get_probability("qwen3b", "coding")
        m.record(RoutingObservation(worker_id="qwen3b", task_type="coding", success=True))
        after = m.get_probability("qwen3b", "coding")
        assert after > before

    def test_failed_observation_decreases_probability(self) -> None:
        m = RoutingMatrix()
        m.seed("qwen3b", "coding", 0.8, total=10)
        before = m.get_probability("qwen3b", "coding")
        m.record(RoutingObservation(worker_id="qwen3b", task_type="coding", success=False))
        after = m.get_probability("qwen3b", "coding")
        assert after < before

    def test_worker_stats(self) -> None:
        m = RoutingMatrix()
        m.seed("w1", "coding", 0.7)
        m.seed("w1", "research", 0.5)
        stats = m.get_worker_stats("w1")
        assert "coding" in stats["task_types"]
        assert "research" in stats["task_types"]

    def test_task_type_stats(self) -> None:
        m = RoutingMatrix()
        m.seed("w1", "coding", 0.7)
        m.seed("w2", "coding", 0.9)
        stats = m.get_task_type_stats("coding")
        assert "w1" in stats["workers"]
        assert "w2" in stats["workers"]

    def test_save_load_roundtrip(self, tmp_path: Path) -> None:
        m = RoutingMatrix()
        m.seed("qwen3b", "coding", 0.8)
        m.record(RoutingObservation(worker_id="qwen3b", task_type="coding", success=True))
        p = str(tmp_path / "matrix.json")
        m.save(p)

        m2 = RoutingMatrix()
        m2.load(p)
        assert abs(m2.get_probability("qwen3b", "coding") - m.get_probability("qwen3b", "coding")) < 0.01

    def test_smoothing_alpha_affects_prior(self) -> None:
        m_strict = RoutingMatrix(smoothing_alpha=0.1)
        m_loose = RoutingMatrix(smoothing_alpha=10.0)
        # Both unseeded: alpha=0.1 → (0.1)/(0.2)=0.5, alpha=10 → (10)/(20)=0.5
        # But after one success:
        m_strict.record(RoutingObservation(worker_id="w", task_type="t", success=True))
        m_loose.record(RoutingObservation(worker_id="w", task_type="t", success=True))
        # Strict: (1+0.1)/(1+0.2) = 0.917, Loose: (1+10)/(1+20) = 0.524
        assert m_strict.get_probability("w", "t") > m_loose.get_probability("w", "t")


# ---------------------------------------------------------------------------
# HistoricalPerformance
# ---------------------------------------------------------------------------

class TestHistoricalPerformance:
    def test_empty_worker_returns_zero_stats(self) -> None:
        hp = HistoricalPerformance()
        stats = hp.get_worker_stats("unknown")
        assert stats.total_tasks == 0
        assert stats.success_rate == 0.0

    def test_record_and_query(self) -> None:
        hp = HistoricalPerformance()
        hp.record(PerformanceEntry(
            worker_id="w1", task_id="T1", task_type="coding",
            success=True, latency_ms=2500, verification_score=0.95,
        ))
        hp.record(PerformanceEntry(
            worker_id="w1", task_id="T2", task_type="coding",
            success=False, latency_ms=3000,
        ))
        stats = hp.get_worker_stats("w1")
        assert stats.total_tasks == 2
        assert stats.successful_tasks == 1
        assert stats.failed_tasks == 1
        assert abs(stats.success_rate - 0.5) < 0.01

    def test_task_type_stats(self) -> None:
        hp = HistoricalPerformance()
        hp.record(PerformanceEntry(worker_id="w1", task_id="T1", task_type="coding", success=True))
        hp.record(PerformanceEntry(worker_id="w2", task_id="T2", task_type="coding", success=False))
        stats = hp.get_task_type_stats("coding")
        assert stats["total"] == 2
        assert stats["successes"] == 1

    def test_recent(self) -> None:
        hp = HistoricalPerformance()
        for i in range(5):
            hp.record(PerformanceEntry(worker_id="w", task_id=f"T{i}", task_type="x", success=True))
        recent = hp.get_recent(2)
        assert len(recent) == 2
        assert recent[-1].task_id == "T4"

    def test_save_load_roundtrip(self, tmp_path: Path) -> None:
        hp = HistoricalPerformance()
        hp.record(PerformanceEntry(
            worker_id="w1", task_id="T1", task_type="coding",
            success=True, latency_ms=1000,
        ))
        p = str(tmp_path / "perf.json")
        hp.save(p)

        hp2 = HistoricalPerformance()
        hp2.load(p)
        stats = hp2.get_worker_stats("w1")
        assert stats.total_tasks == 1
        assert stats.successful_tasks == 1
