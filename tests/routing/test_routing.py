"""META-1B: Routing engine tests — skill registry, worker registry, capability matcher, router.

Tests verify:
  - SkillRegistry CRUD + capability matching + negative filtering
  - WorkerRegistry CRUD + find + escalate + availability filtering
  - CapabilityMatcher scoring (capability, specificity, risk, context)
  - Router end-to-end: task → candidates → selection → audit trail
  - RouteDecision serialization
  - No worker is selected when all are blocked
  - Escalation triggers when score < threshold
"""

from __future__ import annotations

from msb_v3.meta.contracts import MetaTask
from msb_v3.meta.routing.capability_matcher import CapabilityMatcher
from msb_v3.meta.routing.route_decision import RouteDecision
from msb_v3.meta.routing.router import Router
from msb_v3.meta.routing.skill_registry import RegisteredSkill, SkillRegistry
from msb_v3.meta.routing.worker_registry import RegisteredWorker, WorkerRegistry

# ---------------------------------------------------------------------------
# SkillRegistry
# ---------------------------------------------------------------------------

class TestSkillRegistry:
    def test_register_and_get(self) -> None:
        reg = SkillRegistry()
        skill = RegisteredSkill(skill_id="google.gcloud", capabilities=["cloud_cli"])
        reg.register(skill)
        assert reg.get("google.gcloud") is skill
        assert reg.get("nonexistent") is None

    def test_unregister(self) -> None:
        reg = SkillRegistry()
        reg.register(RegisteredSkill(skill_id="s1"))
        assert reg.unregister("s1") is True
        assert reg.unregister("s1") is False
        assert reg.get("s1") is None

    def test_match_by_capabilities(self) -> None:
        reg = SkillRegistry()
        reg.register(RegisteredSkill(skill_id="gcloud", capabilities=["cloud", "infra"]))
        reg.register(RegisteredSkill(skill_id="local", capabilities=["python", "testing"]))
        matches = reg.match(capabilities=["cloud"])
        assert len(matches) == 1
        assert matches[0].skill_id == "gcloud"

    def test_negative_filter_excludes(self) -> None:
        reg = SkillRegistry()
        reg.register(RegisteredSkill(skill_id="gcloud", capabilities=["cloud", "local_msb"]))
        reg.register(RegisteredSkill(skill_id="remote", capabilities=["cloud"]))
        matches = reg.match(capabilities=["cloud"], negative_filter=["local_msb"])
        assert len(matches) == 1
        assert matches[0].skill_id == "remote"

    def test_risk_tier_filter(self) -> None:
        reg = SkillRegistry()
        reg.register(RegisteredSkill(skill_id="safe", capabilities=["code"], risk_tier=1))
        reg.register(RegisteredSkill(skill_id="risky", capabilities=["code"], risk_tier=4))
        matches = reg.match(capabilities=["code"], max_risk_tier=2)
        assert len(matches) == 1
        assert matches[0].skill_id == "safe"

    def test_list_by_provider(self) -> None:
        reg = SkillRegistry()
        reg.register(RegisteredSkill(skill_id="g1", provider="google"))
        reg.register(RegisteredSkill(skill_id="l1", provider="local"))
        assert len(reg.list_by_provider("google")) == 1

    def test_provides_and_blocks(self) -> None:
        s = RegisteredSkill(skill_id="s", capabilities=["a", "b"], negative_capabilities=["c"])
        assert s.provides("a") is True
        assert s.provides("z") is False
        assert s.blocks("c") is True
        assert s.blocks("a") is False


# ---------------------------------------------------------------------------
# WorkerRegistry
# ---------------------------------------------------------------------------

def _make_worker(wid: str, model: str = "", caps=None, preferred=None, avail=True, ctx=8192) -> RegisteredWorker:
    return RegisteredWorker(
        worker_id=wid,
        model_id=model,
        capabilities=caps or [],
        preferred_task_types=preferred or [],
        available=avail,
        max_context_tokens=ctx,
    )


class TestWorkerRegistry:
    def test_register_and_get(self) -> None:
        reg = WorkerRegistry()
        w = _make_worker("qwen3b", "qwen3-3b")
        reg.register(w)
        assert reg.get("qwen3b") is w

    def test_find_by_capabilities(self) -> None:
        reg = WorkerRegistry()
        reg.register(_make_worker("w1", caps=["python", "testing"]))
        reg.register(_make_worker("w2", caps=["cloud"]))
        found = reg.find_workers(capabilities=["python"])
        assert len(found) == 1
        assert found[0].worker_id == "w1"

    def test_find_filters_unavailable(self) -> None:
        reg = WorkerRegistry()
        reg.register(_make_worker("w1", avail=True))
        reg.register(_make_worker("w2", avail=False))
        found = reg.find_workers(available_only=True)
        assert len(found) == 1

    def test_find_by_task_type(self) -> None:
        reg = WorkerRegistry()
        reg.register(_make_worker("w1", preferred=["implementation"]))
        reg.register(_make_worker("w2", preferred=["research"]))
        found = reg.find_workers(task_type="implementation")
        assert len(found) == 1
        assert found[0].worker_id == "w1"

    def test_escalate_qwen3b_to_8b(self) -> None:
        reg = WorkerRegistry()
        reg.register(_make_worker("q3b", model="qwen3-3b"))
        reg.register(_make_worker("q8b", model="qwen3-8b"))
        reg.register(_make_worker("ds", model="deepseek-v3"))
        target = reg.escalate("q3b")
        assert target is not None
        assert target.model_id == "qwen3-8b"

    def test_escalate_returns_none_at_top(self) -> None:
        reg = WorkerRegistry()
        reg.register(_make_worker("human", model="human"))
        assert reg.escalate("human") is None

    def test_overwrite_on_same_id(self) -> None:
        reg = WorkerRegistry()
        reg.register(_make_worker("w1", caps=["a"]))
        reg.register(_make_worker("w1", caps=["b"]))
        w = reg.get("w1")
        assert w is not None
        assert "b" in w.capabilities


# ---------------------------------------------------------------------------
# CapabilityMatcher
# ---------------------------------------------------------------------------

class TestCapabilityMatcher:
    def test_scores_worker_with_matching_caps_higher(self) -> None:
        matcher = CapabilityMatcher()
        task = MetaTask(
            task_id="T1", objective="Implement python code",
            task_type="implementation",
            metadata={"required_capabilities": ["python"]},
        )
        w_good = _make_worker("w1", caps=["python", "code"])
        w_bad = _make_worker("w2", caps=["cloud"])
        results = matcher.match(task, [w_bad, w_good])
        assert results[0].worker_id == "w1"
        assert results[0].capability_score > results[1].capability_score

    def test_blocked_worker_has_zero_overall(self) -> None:
        matcher = CapabilityMatcher()
        w = RegisteredWorker(
            worker_id="w1", capabilities=["python"],
            negative_capabilities=["network"],
        )
        task_with_net = MetaTask(
            task_id="T1", objective="X",
            metadata={"required_capabilities": ["network"]},
        )
        results = matcher.match(task_with_net, [w])
        assert results[0].blocked is True

    def test_unavailable_worker_gets_zero_availability(self) -> None:
        matcher = CapabilityMatcher()
        task = MetaTask(task_id="T1", objective="X")
        w = _make_worker("w1", avail=False)
        results = matcher.match(task, [w])
        assert results[0].availability_score == 0.0

    def test_specificity_bonus_for_preferred_task_type(self) -> None:
        matcher = CapabilityMatcher()
        task = MetaTask(task_id="T1", objective="X", task_type="implementation")
        w_preferred = _make_worker("w1", preferred=["implementation"])
        w_general = _make_worker("w2", preferred=[])
        r1 = matcher.match(task, [w_preferred])[0]
        r2 = matcher.match(task, [w_general])[0]
        assert r1.specificity_score > r2.specificity_score


# ---------------------------------------------------------------------------
# Router (end-to-end)
# ---------------------------------------------------------------------------

class TestRouter:
    def _build_registry(self) -> WorkerRegistry:
        reg = WorkerRegistry()
        reg.register(_make_worker("qwen3b", model="qwen3-3b", caps=["python", "testing"], preferred=["implementation"]))
        reg.register(_make_worker("qwen8b", model="qwen3-8b", caps=["python", "testing", "research"], preferred=["implementation", "analysis"]))
        reg.register(_make_worker("deepseek", model="deepseek-v3", caps=["python", "testing", "research", "cloud"]))
        return reg

    def test_routes_to_best_worker(self) -> None:
        reg = self._build_registry()
        router = Router(worker_registry=reg)
        task = MetaTask(task_id="T1", objective="Implement X", task_type="implementation")
        decision = router.route(task)
        assert decision.is_selected
        assert decision.selected_worker_id is not None

    def test_no_workers_returns_empty(self) -> None:
        reg = WorkerRegistry()
        router = Router(worker_registry=reg)
        task = MetaTask(task_id="T1", objective="X")
        decision = router.route(task)
        assert decision.is_selected is False
        assert "no available workers" in decision.reason

    def test_audit_trail_has_all_candidates(self) -> None:
        reg = self._build_registry()
        router = Router(worker_registry=reg)
        task = MetaTask(task_id="T1", objective="X", task_type="implementation")
        decision = router.route(task)
        assert len(decision.candidates) == 3

    def test_escalation_triggered_below_threshold(self) -> None:
        reg = WorkerRegistry()
        # Worker with very low capability match.
        reg.register(_make_worker("w1", caps=["unrelated"]))
        router = Router(worker_registry=reg, escalation_threshold=0.9)
        task = MetaTask(task_id="T1", objective="Implement python", task_type="implementation")
        decision = router.route(task)
        assert decision.escalation_triggered is True

    def test_decision_serializes(self) -> None:
        reg = self._build_registry()
        router = Router(worker_registry=reg)
        task = MetaTask(task_id="T1", objective="X", task_type="implementation")
        decision = router.route(task)
        d = decision.to_dict()
        assert d["task_id"] == "T1"
        assert "candidates" in d
        assert isinstance(d["candidates"], list)


# ---------------------------------------------------------------------------
# RouteDecision
# ---------------------------------------------------------------------------

class TestRouteDecision:
    def test_is_selected(self) -> None:
        d = RouteDecision(task_id="T1", selected_worker_id="w1")
        assert d.is_selected is True

    def test_not_selected_when_none(self) -> None:
        d = RouteDecision(task_id="T1")
        assert d.is_selected is False

    def test_rejection_reasons(self) -> None:
        from msb_v3.meta.routing.route_decision import RouteCandidate
        d = RouteDecision(
            task_id="T1",
            candidates=[
                RouteCandidate(worker_id="w1", blocked=True, block_reasons=["neg_cap"]),
                RouteCandidate(worker_id="w2", blocked=False),
            ],
        )
        assert "neg_cap" in d.rejection_reasons
