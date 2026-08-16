"""LLM-backed MoIE experts + diverse reviewer panel (completion blueprint).

Hermetic throughout: fake clients stand in for the model seam, so the
invariant, parser, and concurrency are proven without Ollama/Qdrant.
"""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

from msb_v3.moie import (
    LLMExpert,
    MoIEController,
    ReviewPanel,
    build_diverse_reviewer_panel,
)
from msb_v3.moie.experts import Expert, ExpertRegistry
from msb_v3.moie.llm_experts import parse_expert_output
from msb_v3.moie.models import ExpertReport


class _FakeClient:
    """A model seam that returns a fixed text (or raises), with a model id."""

    def __init__(self, text: str, model: str = "fake-model", fail: bool = False):
        self._text = text
        self.model = model
        self._fail = fail

    def generate(self, prompt, *, system=None, **kw):
        if self._fail:
            raise ConnectionError("model down")
        return SimpleNamespace(text=self._text, model=self.model)


def _expert(model: str = "qwen3:8b", text: str = "VERDICT: SAFE", fail: bool = False) -> LLMExpert:
    return LLMExpert(
        expert_id="llm-security",
        name="Security Reviewer",
        model=model,
        description="refute for security defects",
        always_on=True,
        client=_FakeClient(text, model=model, fail=fail),
    )


# --- parser -----------------------------------------------------------------


def test_parse_expert_output_block():
    verdict, risks, mitigations, assumptions, explicit = parse_expert_output(
        "VERDICT: BLOCK\nRISK: shell injection\nMITIGATION: allowlist commands\nASSUMPTION: input is trusted\n"
    )
    assert verdict == "BLOCK"
    assert explicit is True
    assert risks == ["shell injection"]
    assert mitigations == ["allowlist commands"]
    assert assumptions == ["input is trusted"]


def test_parse_expert_output_no_verdict_fails_closed():
    verdict, risks, mitigations, assumptions, explicit = parse_expert_output("here is some prose")
    assert verdict == "CONCERN"
    assert explicit is False


def test_parse_expert_output_case_and_punct_tolerant():
    verdict, _, _, _, explicit = parse_expert_output("verdict: safe.\nrisk: minor\n")
    assert verdict == "SAFE"
    assert explicit is True


# --- LLMExpert --------------------------------------------------------------


def test_llm_expert_block_report_carries_model():
    report = _expert(text="VERDICT: BLOCK\nRISK: auth bypass\n").analyze("add a login page")
    assert report.verdict == "BLOCK"
    assert report.model == "qwen3:8b"
    assert "qwen3:8b" in report.expert_name
    assert any("auth bypass" in r for r in report.risks)


def test_llm_expert_unparseable_fails_closed_to_concern():
    report = _expert(text="i think it's fine, ship it").analyze("change the schema")
    assert report.verdict == "CONCERN"
    assert report.confidence == 0.4
    assert "no parseable verdict" in report.summary


def test_llm_expert_unreachable_model_fails_closed():
    report = _expert(fail=True).analyze("change the schema")
    assert report.verdict == "CONCERN"
    assert "unavailable" in report.summary or "unreachable" in report.summary
    assert report.model == "qwen3:8b"


# --- ReviewPanel invariant --------------------------------------------------


def test_review_panel_rejects_builder_as_reviewer():
    try:
        ReviewPanel(builder_model="claude", experts=(_expert(model="claude"),))
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "builder != reviewer" in str(exc)


def test_review_panel_rejects_duplicate_reviewer_models():
    try:
        ReviewPanel(
            builder_model="claude",
            experts=(_expert(model="qwen3:8b"), _expert(model="qwen3:8b")),
        )
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "pairwise distinct" in str(exc)


def test_review_panel_accepts_distinct_models():
    panel = ReviewPanel(
        builder_model="claude",
        experts=(_expert(model="qwen3:8b"), _expert(model="deepseek-r1")),
    )
    assert panel.reviewer_models == ("qwen3:8b", "deepseek-r1")


def test_build_diverse_reviewer_panel_cycles_lenses():
    panel = build_diverse_reviewer_panel(
        builder_model="claude",
        models=["qwen3:8b", "deepseek-r1", "codegemma:7b"],
        client_factory=lambda model: _FakeClient("VERDICT: SAFE", model=model),
    )
    assert [e.model for e in panel.experts] == ["qwen3:8b", "deepseek-r1", "codegemma:7b"]
    assert [e.lens for e in panel.experts] == ["security", "correctness", "maintainability"]
    assert panel.builder_model == "claude"


def test_build_diverse_reviewer_panel_reads_env(monkeypatch):
    monkeypatch.setenv("MSB_REVIEWER_MODELS", "qwen3:8b, deepseek-r1")
    panel = build_diverse_reviewer_panel(builder_model="claude")
    assert [e.model for e in panel.experts] == ["qwen3:8b", "deepseek-r1"]


# --- panel runs through the controller --------------------------------------


def test_review_panel_controller_runs_every_reviewer():
    panel = build_diverse_reviewer_panel(
        builder_model="claude",
        models=["qwen3:8b", "deepseek-r1"],
        client_factory=lambda model: _FakeClient("VERDICT: SAFE", model=model),
    )
    decision = panel.controller().analyze("add a multiply function")
    assert {r.expert_id for r in decision.reports} == {"llm-security", "llm-correctness"}
    assert decision.verdict == "APPROVE"
    assert {r.model for r in decision.reports} == {"qwen3:8b", "deepseek-r1"}


# --- parallel aanalyze ------------------------------------------------------


def test_aanalyze_matches_analyze_output():
    panel = build_diverse_reviewer_panel(
        builder_model="claude",
        models=["qwen3:8b", "deepseek-r1"],
        client_factory=lambda model: _FakeClient("VERDICT: SAFE\nRISK: x\n", model=model),
    )
    controller = panel.controller()
    sync = controller.analyze("add a multiply function")
    async_ = asyncio.run(controller.aanalyze("add a multiply function"))
    assert sync.verdict == async_.verdict == "APPROVE"
    assert [r.expert_id for r in sync.reports] == [r.expert_id for r in async_.reports]


def test_aanalyze_runs_experts_concurrently():
    # Three slow experts: sequential would take ~3*0.1s, parallel ~0.1s. We
    # assert on max-concurrency (>= 2) so the test is timing-tolerant.
    active = [0]
    max_active = [0]
    lock = threading.Lock()

    def _slow_expert(i: int) -> Expert:
        class _Slow(Expert):
            expert_id = f"slow-{i}"
            name = f"Slow {i}"
            description = ""
            always_on = True

            def analyze(self, claim, context=None):
                with lock:
                    active[0] += 1
                    max_active[0] = max(max_active[0], active[0])
                time.sleep(0.1)
                with lock:
                    active[0] -= 1
                return ExpertReport(
                    expert_id=self.expert_id, expert_name=self.name,
                    verdict="SAFE", confidence=0.6,
                )

        return _Slow()

    registry = ExpertRegistry(experts=tuple(_slow_expert(i) for i in range(3)))
    decision = asyncio.run(MoIEController(registry=registry).aanalyze("print hello"))
    assert decision.verdict == "APPROVE"
    assert len(decision.reports) == 3
    assert max_active[0] >= 2  # actually ran in parallel, not serialized
