"""Unit tests for Ralph Loop harness — Phase 1 stabilization."""
from __future__ import annotations

import json
import tempfile

import pytest

from msb_v3.agent.execution_loop import (
    CircuitBreakerTripped,
    Constraints,
    IntegrityLocks,
    LoopMemory,
    RalphLoopHarness,
    ResourceUsage,
    Status,
    create_ralph_loop,
)

# =============================================================================
# Serialization round-trip
# =============================================================================

class TestSerialization:
    def test_save_load_identical_json(self):
        """status.json -> load() -> Status object -> save() -> identical JSON."""
        with tempfile.TemporaryDirectory() as td:
            loop = create_ralph_loop(workdir=td)
            original = Status(
                loop_id="test-1",
                status="READY",
                result={"key": "value"},
                integrity_locks=IntegrityLocks(scope_hash="abc123", artifacts_hash="def456"),
                constraints=Constraints(budget_cap_usd=2.5, max_loops=8, stall_threshold=4),
                resources=ResourceUsage(prompt_tokens=100, completion_tokens=50, cpu_seconds=1.2),
                logs=["entry one", "entry two"],
                iterations=3,
            )
            loop._write_status(original)
            raw = loop._status_path.read_text()
            loaded = loop._read_status()
            loop._write_status(loaded)
            raw2 = loop._status_path.read_text()
            assert raw == raw2

    def test_nested_dataclass_reconstruction(self):
        with tempfile.TemporaryDirectory() as td:
            loop = create_ralph_loop(workdir=td)
            status = Status(
                loop_id="nested",
                integrity_locks=IntegrityLocks(scope_hash="scope"),
                constraints=Constraints(budget_cap_usd=5.0),
                resources=ResourceUsage(prompt_tokens=10),
            )
            loop._write_status(status)
            loaded = loop._read_status()
            assert isinstance(loaded.integrity_locks, IntegrityLocks)
            assert loaded.integrity_locks.scope_hash == "scope"
            assert isinstance(loaded.constraints, Constraints)
            assert loaded.constraints.budget_cap_usd == 5.0
            assert isinstance(loaded.resources, ResourceUsage)
            assert loaded.resources.prompt_tokens == 10

    def test_empty_values(self):
        with tempfile.TemporaryDirectory() as td:
            loop = create_ralph_loop(workdir=td)
            status = Status()
            loop._write_status(status)
            loaded = loop._read_status()
            assert loaded.loop_id == ""
            assert loaded.iterations == 0
            assert loaded.result == {}
            assert loaded.integrity_locks.scope_hash == ""
            assert loaded.constraints.budget_cap_usd == 1.0

    def test_corrupted_json_restores_backup(self):
        with tempfile.TemporaryDirectory() as td:
            loop = create_ralph_loop(workdir=td)
            # Write valid status, then valid backup, then corrupt primary
            good = Status(loop_id="good", iterations=5)
            loop._write_status(good)
            # Corrupt primary
            loop._status_path.write_text("NOT_JSON")
            loaded = loop._read_status()
            # Should restore from backup, preserving iterations
            assert loaded.iterations == 5
            assert loaded.loop_id == "good"

    def test_corrupted_json_no_backup_falls_back_to_default(self):
        with tempfile.TemporaryDirectory() as td:
            loop = create_ralph_loop(workdir=td)
            loop._status_path.write_text("NOT_JSON")
            loaded = loop._read_status()
            assert loaded.iterations == 0
            assert loaded.status == "READY"


# =============================================================================
# Atomic persistence
# =============================================================================

class TestAtomicPersistence:
    def test_status_file_exists_after_write(self):
        with tempfile.TemporaryDirectory() as td:
            loop = create_ralph_loop(workdir=td)
            loop._write_status(Status(loop_id="atomic"))
            assert loop._status_path.exists()
            assert loop._backup_path.exists()

    def test_no_partial_read_after_write(self):
        """STATUS.json should never be half-written."""
        with tempfile.TemporaryDirectory() as td:
            loop = create_ralph_loop(workdir=td)
            for i in range(10):
                loop._write_status(Status(loop_id=f"iter-{i}", iterations=i))
                data = json.loads(loop._status_path.read_text())
                assert data["loop_id"] == f"iter-{i}"
                assert data["iterations"] == i


# =============================================================================
# Artifact hashing
# =============================================================================

class TestArtifactHashing:
    def test_empty_dir_hash_is_stable(self):
        with tempfile.TemporaryDirectory() as td:
            loop = create_ralph_loop(workdir=td)
            h1 = loop._compute_artifacts_hash()
            h2 = loop._compute_artifacts_hash()
            assert h1 == h2
            assert len(h1) == 16

    def test_hash_changes_with_content(self):
        with tempfile.TemporaryDirectory() as td:
            loop = create_ralph_loop(workdir=td)
            h1 = loop._compute_artifacts_hash()
            (loop._artifacts_dir / "a.txt").write_text("alpha")
            h2 = loop._compute_artifacts_hash()
            assert h1 != h2

    def test_hash_changes_with_filename(self):
        with tempfile.TemporaryDirectory() as td:
            loop = create_ralph_loop(workdir=td)
            (loop._artifacts_dir / "a.txt").write_text("same")
            h1 = loop._compute_artifacts_hash()
            (loop._artifacts_dir / "b.txt").write_text("same")
            h2 = loop._compute_artifacts_hash()
            assert h1 != h2  # filename affects hash

    def test_hash_stable_across_writes(self):
        with tempfile.TemporaryDirectory() as td:
            loop = create_ralph_loop(workdir=td)
            (loop._artifacts_dir / "x.txt").write_text("content")
            h1 = loop._compute_artifacts_hash()
            h2 = loop._compute_artifacts_hash()
            assert h1 == h2


# =============================================================================
# Scope lock
# =============================================================================

class TestScopeLock:
    def test_scope_bound_on_first_run(self):
        with tempfile.TemporaryDirectory() as td:
            loop = create_ralph_loop(workdir=td)
            status = Status()
            loop._bind_scope(status, "mission alpha")
            assert status.integrity_locks.scope_hash != ""

    def test_scope_evolution_updates_hash(self):
        with tempfile.TemporaryDirectory() as td:
            loop = create_ralph_loop(workdir=td)
            status = Status()
            loop._bind_scope(status, "mission alpha")
            original_hash = status.integrity_locks.scope_hash
            loop._evolve_scope(status, "added: budget tracking")
            evolved_hash = status.integrity_locks.scope_hash
            assert original_hash != evolved_hash
            assert len(status.result["evolved_scope"]) == 1

    def test_scope_static_evolution(self):
        status = Status()
        RalphLoopHarness._evolve_scope_static(status, "new scope item")
        assert len(status.result["evolved_scope"]) == 1
        assert status.result["evolved_scope"][0]["new_scope"] == "new scope item"


# =============================================================================
# Resource accounting
# =============================================================================

class TestResourceAccounting:
    def test_account_resources_updates_budget(self):
        with tempfile.TemporaryDirectory() as td:
            loop = create_ralph_loop(workdir=td)
            status = Status(constraints=Constraints(budget_cap_usd=10.0))
            loop.account_resources(
                status,
                prompt_tokens=1000,
                completion_tokens=500,
                cpu_seconds=2.0,
                gpu_seconds=1.0,
                disk_writes=3,
            )
            assert status.resources.prompt_tokens == 1000
            assert status.resources.completion_tokens == 500
            assert status.resources.cpu_seconds == 2.0
            assert status.resources.gpu_seconds == 1.0
            assert status.resources.disk_writes == 3
            # 500 completion tokens -> 500/1000 * 0.001 = 0.0005
            assert abs(status.constraints.budget_spent_usd - 0.0005) < 1e-9

    def test_account_resources_accumulates(self):
        with tempfile.TemporaryDirectory() as td:
            loop = create_ralph_loop(workdir=td)
            status = Status(constraints=Constraints(budget_cap_usd=10.0))
            loop.account_resources(status, prompt_tokens=100)
            loop.account_resources(status, prompt_tokens=200)
            assert status.resources.prompt_tokens == 300


# =============================================================================
# Concurrency / locking
# =============================================================================

class TestConcurrency:
    def test_acquire_release_lock(self):
        with tempfile.TemporaryDirectory() as td:
            loop = create_ralph_loop(workdir=td)
            assert loop._acquire_lock() is True
            assert loop._lock_path.exists()
            loop._release_lock()
            assert not loop._lock_path.exists()

    def test_double_acquire_fails(self):
        with tempfile.TemporaryDirectory() as td:
            loop = create_ralph_loop(workdir=td)
            assert loop._acquire_lock() is True
            assert loop._acquire_lock() is False
            loop._release_lock()


# =============================================================================
# Journaling
# =============================================================================

class TestJournaling:
    def test_journal_creates_file(self):
        with tempfile.TemporaryDirectory() as td:
            loop = create_ralph_loop(workdir=td)
            loop._journal("test_event key=value")
            assert loop._journal_path.exists()
            lines = loop._journal_path.read_text().strip().splitlines()
            assert len(lines) == 1
            entry = json.loads(lines[0])
            assert entry["event"] == "test_event key=value"

    def test_journal_appends(self):
        with tempfile.TemporaryDirectory() as td:
            loop = create_ralph_loop(workdir=td)
            loop._journal("event1")
            loop._journal("event2")
            lines = loop._journal_path.read_text().strip().splitlines()
            assert len(lines) == 2


# =============================================================================
# End-to-end: demo endpoint behavior
# =============================================================================

class TestRalphLoopExecution:
    def test_demo_completes_in_three_iterations(self):
        loop = create_ralph_loop()
        seen = []

        def action_fn(goal, status, context):
            seen.append(status.iterations)
            artifact = loop._artifacts_dir / f"finding_{status.iterations:03d}.md"
            artifact.write_text(f"# Finding {status.iterations}\n")
            context["resources"] = {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "cpu_seconds": 0.1,
                "gpu_seconds": 0.05,
            }
            if status.iterations >= 3:
                return "[DONE] Completed", None
            return f"iteration {status.iterations}", None

        result = loop.execute("demo", action_fn=action_fn, session="demo")
        assert result.ok is True
        assert result.event == "ralph_loop:completed"
        assert result.payload["iterations"] == 3
        assert len(seen) == 3
        # Artifacts were written
        assert (loop._artifacts_dir / "finding_001.md").exists()
        assert (loop._artifacts_dir / "finding_002.md").exists()
        assert (loop._artifacts_dir / "finding_003.md").exists()
        # Resource accounting present in telemetry
        assert "resources" in result.telemetry
        assert result.telemetry["resources"]["completion_tokens"] > 0

    def test_exhausts_max_loops_without_completion(self):
        with tempfile.TemporaryDirectory() as td:
            loop = create_ralph_loop(workdir=td)
            # Pre-seed status with max_loops=2 via disk write
            from msb_v3.agent.execution_loop import Constraints as C
            loop._write_status(Status(loop_id="exhaust", constraints=C(max_loops=2)))

            def action_fn(goal, status, context):
                return "still working", None

            result = loop.execute("exhaust", action_fn=action_fn, session="exhaust")
            assert result.ok is False
            assert result.event == "ralph_loop:exhausted"
            assert "2" in result.error

    def test_circuit_breaker_budget(self):
        with tempfile.TemporaryDirectory() as td:
            loop = create_ralph_loop(workdir=td)
            # Pre-seed status with budget already over cap
            from msb_v3.agent.execution_loop import Constraints as C2
            loop._write_status(Status(loop_id="bb", constraints=C2(budget_cap_usd=0.0, max_loops=5, budget_spent_usd=1.0)))

            def action_fn(goal, status, context):
                return "work", None

            with pytest.raises(CircuitBreakerTripped) as exc_info:
                loop.execute("budget", action_fn=action_fn, session="bb")
            assert "BUDGET" in str(exc_info.value)


# =============================================================================
# Phase 2 — Integrity Layer
# =============================================================================

class TestMissionIntegrity:
    def test_hash_mission_deterministic(self):
        h1 = RalphLoopHarness.hash_mission("m1", "ethics1", ["tool_a", "tool_b"])
        h2 = RalphLoopHarness.hash_mission("m1", "ethics1", ["tool_b", "tool_a"])
        assert h1 == h2
        assert len(h1) == 16

    def test_hash_mission_changes_with_content(self):
        h1 = RalphLoopHarness.hash_mission("m1", "ethics1", ["tool_a"])
        h2 = RalphLoopHarness.hash_mission("m2", "ethics1", ["tool_a"])
        assert h1 != h2

    def test_bind_mission_populates_status(self):
        with tempfile.TemporaryDirectory() as td:
            loop = create_ralph_loop(workdir=td)
            status = Status()
            loop._bind_mission(status, "mission alpha", "be_safe", ["search", "write"])
            assert status.integrity_locks.mission_hash != ""
            assert status.integrity_locks.ethics_hash != ""
            assert status.integrity_locks.allowed_tools_hash != ""
            assert status.result["mission"] == "mission alpha"
            assert status.result["ethics"] == "be_safe"
            assert status.result["allowed_tools"] == ["search", "write"]

    def test_bind_mission_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            loop = create_ralph_loop(workdir=td)
            status = Status()
            loop._bind_mission(status, "mission1", "ethics1", ["t1"])
            first_hash = status.integrity_locks.mission_hash
            loop._bind_mission(status, "mission1", "ethics1", ["t1"])
            assert status.integrity_locks.mission_hash == first_hash


# =============================================================================
# Phase 5 — Evaluation Layer
# =============================================================================

class TestEvaluation:
    def test_score_bounds(self):
        scores = [
            RalphLoopHarness.evaluate("word " * 200, 3, "aaa", "bbb"),
            RalphLoopHarness.evaluate("", 0, "aaa", "aaa"),
            RalphLoopHarness.evaluate("[DONE]", 5, "x", "y"),
        ]
        for s in scores:
            assert 0.0 <= s["score"] <= 1.0

    def test_high_artifacts_boosts_completeness(self):
        low = RalphLoopHarness.evaluate("some output", 0, "a", "b")
        high = RalphLoopHarness.evaluate("some output", 5, "a", "b")
        assert high["completeness"] > low["completeness"]

    def test_done_signal_boosts_correctness(self):
        normal = RalphLoopHarness.evaluate("regular output", 1, "a", "b")
        done = RalphLoopHarness.evaluate("[DONE] regular output", 1, "a", "b")
        assert done["correctness"] >= normal["correctness"]

    def test_changed_flag_reflects_hash_diff(self):
        changed = RalphLoopHarness.evaluate("output", 1, "hash_a", "hash_b")
        unchanged = RalphLoopHarness.evaluate("output", 1, "hash_a", "hash_a")
        assert changed["changed"] is True
        assert unchanged["changed"] is False


# =============================================================================
# Phase 6 — Self-Improvement Loop
# =============================================================================

class TestSelfImprovement:
    def test_high_score_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            loop = create_ralph_loop(workdir=td)
            status = Status(loop_id="si1")
            eval_scores = {"score": 0.9, "novelty": 0.9, "correctness": 1.0, "completeness": 1.0, "evidence": 1.0, "confidence": 0.75, "risk": 0.1}
            patch = loop._maybe_improve(status, eval_scores)
            assert patch is None
            assert status.result["improvement_log"][-1]["action"] == "accept"

    def test_low_score_triggers_patch_search(self):
        with tempfile.TemporaryDirectory() as td:
            loop = create_ralph_loop(workdir=td)
            status = Status(loop_id="si2")
            eval_scores = {"score": 0.2, "novelty": 0.1, "correctness": 0.1, "completeness": 0.1, "evidence": 0.0, "confidence": 0.1, "risk": 0.8}
            patch = loop._maybe_improve(status, eval_scores)
            assert patch is not None
            assert "patch_search" in status.result["improvement_log"][-1]["action"]

    def test_patch_detects_weak_axes(self):
        with tempfile.TemporaryDirectory() as td:
            loop = create_ralph_loop(workdir=td)
            status = Status(loop_id="si3")
            eval_scores = {"score": 0.2, "evidence": 0.1, "risk": 0.8}
            patch = loop._propose_patch(status, eval_scores)
            assert "evidence" in patch or "risk" in patch or patch == "increase_artifact_diversity"

    def test_improvement_log_appended(self):
        with tempfile.TemporaryDirectory() as td:
            loop = create_ralph_loop(workdir=td)
            status = Status(loop_id="si4")
            loop._maybe_improve(status, {"score": 0.95, "correctness": 1.0})
            loop._maybe_improve(status, {"score": 0.2, "correctness": 0.1})
            assert len(status.result["improvement_log"]) == 2
            assert status.result["improvement_log"][0]["action"] == "accept"
            assert status.result["improvement_log"][1]["action"] == "patch_search"


# =============================================================================
# Phase 8 — Memory System
# =============================================================================

class TestLoopMemory:
    def test_record_and_recall(self):
        with tempfile.TemporaryDirectory() as td:
            mem = LoopMemory(memory_dir=td)
            mem.record("run-1", {"iterations": 3, "status": "completed"})
            entry = mem.recall("run-1")
            assert entry is not None
            assert entry["summary"]["iterations"] == 3

    def test_recall_missing_returns_none(self):
        with tempfile.TemporaryDirectory() as td:
            mem = LoopMemory(memory_dir=td)
            assert mem.recall("nonexistent") is None

    def test_search_returns_ranked_results(self):
        with tempfile.TemporaryDirectory() as td:
            mem = LoopMemory(memory_dir=td)
            mem.record("alpha", {"topic": "sovereign stack"})
            mem.record("beta", {"topic": "logistics"})
            mem.record("gamma", {"topic": "sovereign architecture"})
            results = mem.search("sovereign", limit=2)
            assert len(results) == 2
            assert results[0]["loop_id"] in ("alpha", "gamma")
            assert results[1]["loop_id"] in ("alpha", "gamma")

    def test_search_empty_when_no_match(self):
        with tempfile.TemporaryDirectory() as td:
            mem = LoopMemory(memory_dir=td)
            mem.record("run-1", {"topic": "unrelated"})
            results = mem.search("zzzzz_no_match")
            assert results == []

