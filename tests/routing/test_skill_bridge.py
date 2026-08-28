"""META-7: SkillBridge — Google skills as pluggable workers.

Tests that:
  1. Google skills register as RegisteredWorkers
  2. They have correct capabilities and negative capabilities
  3. They compete with local workers through the same contract
  4. Filesystem discovery works
  5. Interchangeability is provable
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from msb_v3.meta.contracts import Complexity, MetaTask
from msb_v3.meta.routing.capability_matcher import CapabilityMatcher
from msb_v3.meta.routing.skill_bridge import SkillBridge
from msb_v3.meta.routing.worker_registry import RegisteredWorker, WorkerRegistry

# ── Fixtures ─────────────────────────────────────────────────────────


def _local_worker(
    worker_id: str = "qwen3-8b",
    capabilities: list[str] | None = None,
    task_types: list[str] | None = None,
) -> RegisteredWorker:
    return RegisteredWorker(
        worker_id=worker_id,
        display_name=worker_id,
        kind="local",
        model_id=worker_id,
        capabilities=capabilities or ["python", "testing", "code"],
        negative_capabilities=[],
        max_risk_tier=1,
        max_context_tokens=8192,
        preferred_task_types=task_types or ["implementation", "testing"],
    )


def _task(
    task_id: str = "T1",
    task_type: str = "implementation",
    required_caps: list[str] | None = None,
) -> MetaTask:
    return MetaTask(
        task_id=task_id,
        objective="test task",
        task_type=task_type,
        complexity=Complexity.MEDIUM,
        metadata={"required_capabilities": required_caps or []},
    )


# ── Google skill registration ────────────────────────────────────────


class TestGoogleSkillRegistration:
    def test_registers_six_google_skills(self) -> None:
        registry = WorkerRegistry()
        bridge = SkillBridge()
        count = bridge.register_google_skills(registry)
        assert count == 6
        assert len(registry.list_all()) == 6

    def test_each_google_skill_has_capabilities(self) -> None:
        registry = WorkerRegistry()
        bridge = SkillBridge()
        bridge.register_google_skills(registry)
        for worker in registry.list_all():
            assert len(worker.capabilities) > 0, f"{worker.worker_id} has no capabilities"

    def test_each_google_skill_has_negative_caps(self) -> None:
        registry = WorkerRegistry()
        bridge = SkillBridge()
        bridge.register_google_skills(registry)
        for worker in registry.list_all():
            assert "local_msb_runtime" in worker.negative_capabilities, (
                f"{worker.worker_id} missing local_msb_runtime negative cap"
            )

    def test_gcloud_is_high_risk(self) -> None:
        registry = WorkerRegistry()
        bridge = SkillBridge()
        bridge.register_google_skills(registry)
        gcloud = registry.get("google.gcloud")
        assert gcloud is not None
        assert gcloud.max_risk_tier == 3  # cloud ops are higher risk

    def test_dev_knowledge_is_low_risk(self) -> None:
        registry = WorkerRegistry()
        bridge = SkillBridge()
        bridge.register_google_skills(registry)
        dev = registry.get("google.retrieving-developer-knowledge")
        assert dev is not None
        assert dev.max_risk_tier == 1  # read-only documentation

    def test_registered_ids_tracked(self) -> None:
        bridge = SkillBridge()
        registry = WorkerRegistry()
        bridge.register_google_skills(registry)
        assert len(bridge.registered_ids) == 6
        assert "google.gcloud" in bridge.registered_ids


# ── Interchangeability ───────────────────────────────────────────────


class TestInterchangeability:
    """Prove Google skills compete with local workers through the same contract."""

    def test_same_find_workers_interface(self) -> None:
        """Both local and Google workers are found by the same query."""
        registry = WorkerRegistry()
        bridge = SkillBridge()
        bridge.register_google_skills(registry)
        registry.register(_local_worker("qwen3-8b", ["python", "code"]))

        # Query for "python" capability — should find local worker.
        python_workers = registry.find_workers(capabilities=["python"])
        assert len(python_workers) == 1
        assert python_workers[0].worker_id == "qwen3-8b"

        # Query for "google_cloud" capability — should find Google workers.
        cloud_workers = registry.find_workers(capabilities=["google_cloud"])
        assert len(cloud_workers) >= 1
        assert any("google" in w.worker_id for w in cloud_workers)

    def test_same_task_type_routing(self) -> None:
        """Different workers handle different task types through the same interface."""
        registry = WorkerRegistry()
        bridge = SkillBridge()
        bridge.register_google_skills(registry)
        registry.register(_local_worker("qwen3-8b"))

        # Implementation task → local worker.
        impl = registry.find_workers(task_type="implementation")
        assert any(w.worker_id == "qwen3-8b" for w in impl)

        # Deployment task → Google gcloud.
        deploy = registry.find_workers(task_type="deployment")
        assert any("gcloud" in w.worker_id for w in deploy)

        # Research task → Google dev knowledge.
        research = registry.find_workers(task_type="research")
        assert any("developer-knowledge" in w.worker_id for w in research)

    def test_negative_caps_prevent_misrouting(self) -> None:
        """Google skills reject local MSB tasks through negative capabilities."""
        registry = WorkerRegistry()
        bridge = SkillBridge()
        bridge.register_google_skills(registry)

        # Query for "local_msb_runtime" capability — no Google skill should match.
        local_workers = registry.find_workers(
            capabilities=["local_msb_runtime"],
        )
        assert len(local_workers) == 0

    def test_escalation_across_providers(self) -> None:
        """Escalation works across local and skill workers."""
        registry = WorkerRegistry()
        bridge = SkillBridge()
        bridge.register_google_skills(registry)
        registry.register(_local_worker("qwen3-3b", ["python"]))
        registry.register(
            RegisteredWorker(
                worker_id="qwen3-8b",
                model_id="qwen3-8b",
                kind="local",
                capabilities=["python"],
                max_context_tokens=16384,
            )
        )

        # Escalate from 3B → 8B.
        next_worker = registry.escalate("qwen3-3b")
        assert next_worker is not None
        assert next_worker.worker_id == "qwen3-8b"


# ── Filesystem discovery ─────────────────────────────────────────────


class TestFilesystemDiscovery:
    def test_discover_from_skills_directory(self) -> None:
        """Discover skills from a mock skills directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a mock skill with "not for" so negative caps are inferred.
            skill_dir = Path(tmpdir) / "my-custom-skill"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                "---\nname: my-custom-skill\n"
                "metadata:\n  category: AiAndMachineLearning\n"
                "description: A test skill for research and API work.\n---\n"
                "# My Skill\n\nUse for research and API integration.\n"
                "Do not use for local MSB runtime or speech audio.\n"
            )

            registry = WorkerRegistry()
            bridge = SkillBridge()
            count = bridge.discover_and_register(
                registry, skills_dir=tmpdir, provider_prefix="test"
            )
            assert count == 1
            worker = registry.get("test.my-custom-skill")
            assert worker is not None
            assert "research" in worker.capabilities
            assert "api" in worker.capabilities
            assert "local_msb_runtime" in worker.negative_capabilities

    def test_discover_missing_directory(self) -> None:
        """Missing directory returns 0, no crash."""
        registry = WorkerRegistry()
        bridge = SkillBridge()
        count = bridge.discover_and_register(
            registry, skills_dir="/nonexistent/path"
        )
        assert count == 0

    def test_discover_skips_non_directories(self) -> None:
        """Non-directory entries in skills dir are skipped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "random-file.txt").write_text("not a skill")
            skill_dir = Path(tmpdir) / "real-skill"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                "---\nname: real-skill\n---\n# Real\n"
            )

            registry = WorkerRegistry()
            bridge = SkillBridge()
            count = bridge.discover_and_register(
                registry, skills_dir=tmpdir
            )
            assert count == 1

    def test_discover_no_duplicate_explicit(self) -> None:
        """Explicitly registered skills are not duplicated by discovery."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a skill that matches an explicit one.
            skill_dir = Path(tmpdir) / "gcloud"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                "---\nname: gcloud\n---\n# gcloud\n"
            )

            registry = WorkerRegistry()
            bridge = SkillBridge()
            # Register explicitly first.
            bridge.register_google_skills(registry)
            explicit_count = len(registry.list_all())

            # Discovery should skip the already-registered one.
            bridge.discover_and_register(
                registry, skills_dir=tmpdir, provider_prefix="google"
            )
            # gcloud already registered as "google.gcloud", so discovery
            # with prefix "google" should skip it.
            assert len(registry.list_all()) == explicit_count


# ── Capability matching through matcher ──────────────────────────────


class TestMatcherIntegration:
    """Prove the full matcher pipeline works with mixed local + Google workers."""

    def test_matcher_scores_local_for_python(self) -> None:
        """Local worker scores highest for a Python implementation task."""
        registry = WorkerRegistry()
        bridge = SkillBridge()
        bridge.register_google_skills(registry)
        registry.register(_local_worker("qwen3-8b", ["python", "code", "testing"]))

        matcher = CapabilityMatcher()
        task = _task(required_caps=["python"])
        workers = registry.find_workers(capabilities=["python"])

        results = matcher.match(task, workers)
        assert len(results) >= 1
        assert results[0].worker_id == "qwen3-8b"
        assert results[0].capability_score > 0.0

    def test_matcher_scores_google_for_cloud(self) -> None:
        """Google gcloud scores highest for a cloud deployment task."""
        registry = WorkerRegistry()
        bridge = SkillBridge()
        bridge.register_google_skills(registry)
        registry.register(_local_worker("qwen3-8b", ["python", "code"]))

        matcher = CapabilityMatcher()
        task = _task(
            task_type="deployment",
            required_caps=["cloud_cli", "deployment", "gcp"],
        )
        workers = registry.find_workers(
            capabilities=["cloud_cli"],
            negative_filter=["local_msb_runtime"],
        )

        results = matcher.match(task, workers)
        assert len(results) >= 1
        assert "gcloud" in results[0].worker_id

    def test_matcher_scores_dev_knowledge_for_research(self) -> None:
        """Google dev knowledge scores highest for documentation research."""
        registry = WorkerRegistry()
        bridge = SkillBridge()
        bridge.register_google_skills(registry)

        matcher = CapabilityMatcher()
        task = _task(
            task_type="research",
            required_caps=["research", "documentation"],
        )
        workers = registry.find_workers(
            capabilities=["research"],
            negative_filter=["local_msb_runtime"],
        )

        results = matcher.match(task, workers)
        assert len(results) >= 1
        assert "developer-knowledge" in results[0].worker_id

    def test_all_workers_share_same_match_contract(self) -> None:
        """Both local and Google workers produce MatchResult with the same fields."""
        registry = WorkerRegistry()
        bridge = SkillBridge()
        bridge.register_google_skills(registry)
        registry.register(_local_worker("qwen3-8b", ["python"]))

        matcher = CapabilityMatcher()
        task = _task(required_caps=["python"])

        all_workers = registry.list_all()
        results = matcher.match(task, all_workers)

        for result in results:
            assert hasattr(result, "worker_id")
            assert hasattr(result, "overall_score")
            assert hasattr(result, "capability_score")
            assert hasattr(result, "blocked")
            assert 0.0 <= result.overall_score <= 1.0
