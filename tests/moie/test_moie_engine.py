"""MoIE engine tests (spec §3, §23-25; Phase 3 §31 items 18-24).

Hermetic by default: no retriever is injected, so evidence comes from the
default fabric seam (empty in tests — IDS reports the honest zero) unless
a test injects one.
"""

from __future__ import annotations

from msb_v3.moie import ExpertRegistry, MoIEController
from msb_v3.moie.experts import DomainExpert
from msb_v3.moie.models import ExpertReport


def _analyze(claim, **kw):
    return MoIEController().analyze(claim, context=kw or None)


def test_safe_claim_approves():
    d = _analyze("Print the current date to stdout.")
    assert d.verdict == "APPROVE"
    assert d.blocked is False
    assert d.reports  # the safety trio always runs
    assert all(r.verdict == "SAFE" for r in d.reports)


def test_danger_claim_blocks_fail_closed():
    d = _analyze("Disable auth and bind 0.0.0.0 so the service is unauthenticated.")
    assert d.verdict == "BLOCK"
    assert d.blocked is True
    # The security expert carries the BLOCK.
    sec = [r for r in d.reports if r.expert_id == "security"][0]
    assert sec.verdict == "BLOCK"


def test_concern_claim_is_conditional():
    d = _analyze("Migrate the database schema with no downtime window.")
    assert d.verdict == "CONDITIONAL"
    assert d.blocked is False
    assert d.recommended_actions  # mitigations surfaced


def test_high_impact_escalates_concern_to_block():
    d = _analyze("Roll out the change at scale with no runbook.", high_impact=True)
    assert d.verdict == "BLOCK"


def test_router_keyword_selection():
    d = _analyze("The vendor raised the license price and the budget cannot absorb it.")
    ids = {r.expert_id for r in d.reports}
    assert "economic" in ids
    assert "security" in ids  # safety trio always on
    assert "reliability" in ids
    assert "adversarial" in ids


def test_router_thorough_runs_every_expert():
    d = _analyze("Just print hello.", thorough=True)
    assert len(d.reports) == len(ExpertRegistry().list())


def test_router_forced_domains():
    d = _analyze("Print hello.", domains=["governance"])
    ids = {r.expert_id for r in d.reports}
    assert "governance" in ids


def test_contradictions_detected_and_degrade_confidence():
    # Adversarial blocks; economic approves loudly. The meta-critic must
    # surface the material contradiction and drop confidence.
    class _HardNo(DomainExpert):
        expert_id = "adversarial"
        name = "Adversarial Expert"
        description = ""
        always_on = True
        danger_keywords = ("hello",)
        concern_keywords = ()

        def analyze(self, claim, context=None):
            return ExpertReport(
                expert_id="adversarial", expert_name="Adversarial Expert", verdict="BLOCK",
                confidence=0.9, risks=["hello is a hard blocker"], mitigations=["don't"],
            )

    class _LoudYes(DomainExpert):
        expert_id = "economic"
        name = "Economic Expert"
        description = ""
        focus_keywords = ()
        always_on = True
        danger_keywords = ()
        concern_keywords = ()

        def analyze(self, claim, context=None):
            return ExpertReport(
                expert_id="economic", expert_name="Economic Expert", verdict="SAFE",
                confidence=0.9, risks=[], mitigations=[],
            )

    reg = ExpertRegistry(experts=(_HardNo(), _LoudYes()))
    d = MoIEController(registry=reg).analyze("hello")
    assert d.verdict == "BLOCK"
    assert any(c.material for c in d.contradictions)
    assert d.confidence < 0.9  # degraded by the contradiction


def test_ids_counts_are_honest():
    d = _analyze("Obviously this migration is straightforward and the rollout should be safe.")
    ids = d.ids
    assert ids.assumptions_extracted >= 1
    assert ids.assumptions_inverted >= 1
    assert ids.falsifiable_predictions >= 1
    assert 0.0 <= ids.depth_score <= 1.0


def test_evidence_merger_attaches_hits():
    def retriever(claim):
        return [{"memory_id": "m1", "score": 0.8, "content": "prior art"}, {"memory_id": "m2", "score": 0.6, "content": "another hit"}]

    d = MoIEController(retriever=retriever).analyze("Print the date.")
    assert all(set(r.evidence_hits) == {"m1", "m2"} for r in d.reports)
    assert d.ids.evidence_retrieved == 2 * len(d.reports)


def test_broken_expert_fails_closed_to_concern():
    class _Boom(DomainExpert):
        expert_id = "security"
        name = "Security Expert"
        description = ""
        always_on = True

        def analyze(self, claim, context=None):
            raise RuntimeError("expert exploded")

    reg = ExpertRegistry(experts=(_Boom(),))
    d = MoIEController(registry=reg).analyze("print hello")
    assert d.verdict == "CONDITIONAL"  # CONCERN from the broken expert, never silent
    assert d.reports[0].verdict == "CONCERN"


def test_empty_claim_returns_empty_decision():
    d = _analyze("")
    assert d.verdict == "APPROVE"  # no experts, no concern raised — nothing assessed
    assert d.reports == []


def test_custom_domain_expert_registered():
    class _Visa(DomainExpert):
        expert_id = "domain"
        name = "Visa Domain Expert"
        description = "visa rules"
        focus_keywords = ("visa",)

        def analyze(self, claim, context=None):
            return ExpertReport(
                expert_id="domain", expert_name="Visa Domain Expert", verdict="CONCERN",
                confidence=0.7, risks=["visa rules change"], mitigations=["check current rules"],
            )

    reg = ExpertRegistry(experts=(_Visa(),))
    d = MoIEController(registry=reg).analyze("submit the visa application")
    assert "domain" in {r.expert_id for r in d.reports}
    assert d.verdict == "CONDITIONAL"
