"""Tests for the automation brain (automation/brain.py).

The four result paths are pinned: dry_run (default, no side effects),
blocked (missing key on approve), created (approve + live client), and
budget refusal (cap exhausted). Clients/LLMs are injected so nothing in
these tests touches a real provider or the network.
"""

from __future__ import annotations

import pytest

from msb_v3.automation.brain import create_automation, plan_automation, try_parse_plan
from msb_v3.automation.budget import BudgetLedger
from msb_v3.automation.manifest import Manifest


class _FakeN8n:
    def __init__(self) -> None:
        self.created: list[dict] = []
        self.activated: list[str] = []

    def available(self) -> bool:
        return True

    def unavailable_reason(self) -> str:
        return ""

    def create_workflow(self, workflow: dict) -> dict:
        self.created.append(workflow)
        return {"id": "wf-1", "name": workflow["name"], "active": False}

    def set_active(self, workflow_id: str, active: bool = True) -> dict:
        self.activated.append(workflow_id)
        return {"id": workflow_id, "active": active}

    def webhook_url(self, path: str) -> str:
        return f"http://n8n/webhook/{path}"


class _MissingKeyN8n:
    def available(self) -> bool:
        return False

    def unavailable_reason(self) -> str:
        return "N8N_API_KEY not set"


PLAN = {"provider": "n8n", "name": "echo bot", "description": "echo webhook payloads"}


def test_plan_automation_with_injected_llm(tmp_path) -> None:
    def llm(messages):
        return '```json\n{"automation": {"provider": "n8n", "name": "echo bot", "description": "echo webhook payloads"}}\n```'

    plan = plan_automation("build a webhook echo", llm=llm)
    assert plan == PLAN


def test_plan_automation_unparseable(tmp_path) -> None:
    with pytest.raises(ValueError):
        plan_automation("make me coffee", llm=lambda messages: "sure, here's some text")
    with pytest.raises(ValueError):
        plan_automation("   ")


def test_create_dry_run_default(tmp_path) -> None:
    budget = BudgetLedger(db_path=str(tmp_path / "budget.db"), cap_usd=10.0)
    manifest = Manifest(path=str(tmp_path / "manifest.jsonl"))
    result = create_automation(PLAN, approve=False, budget=budget, manifest=manifest, client_factory=lambda p: _FakeN8n())
    assert result["status"] == "dry_run"
    assert result["ok"] is True
    # No creation happened; the manifest records the dry-run plan.
    rows = manifest.list()
    assert len(rows) == 1 and rows[0]["status"] == "dry_run"
    assert budget.spent() > 0  # the LLM plan estimate was recorded


def test_create_blocked_when_key_missing(tmp_path) -> None:
    budget = BudgetLedger(db_path=str(tmp_path / "budget.db"), cap_usd=10.0)
    manifest = Manifest(path=str(tmp_path / "manifest.jsonl"))
    result = create_automation(PLAN, approve=True, budget=budget, manifest=manifest, client_factory=lambda p: _MissingKeyN8n())
    assert result["status"] == "blocked"
    assert result["ok"] is False
    assert "N8N_API_KEY" in result["summary"]
    assert manifest.list()[0]["status"] == "blocked"


def test_create_created_with_approval(tmp_path) -> None:
    budget = BudgetLedger(db_path=str(tmp_path / "budget.db"), cap_usd=10.0)
    manifest = Manifest(path=str(tmp_path / "manifest.jsonl"))
    fake = _FakeN8n()
    result = create_automation(PLAN, approve=True, budget=budget, manifest=manifest, client_factory=lambda p: fake)
    assert result["status"] == "created"
    assert result["ok"] is True
    assert len(fake.created) == 1
    assert fake.activated == ["wf-1"]
    assert result["detail"]["webhook_url"].startswith("http://n8n/webhook/")
    assert manifest.list()[0]["status"] == "created"


def test_create_refused_when_budget_exhausted(tmp_path) -> None:
    budget = BudgetLedger(db_path=str(tmp_path / "budget.db"), cap_usd=0.001)
    manifest = Manifest(path=str(tmp_path / "manifest.jsonl"))
    fake = _FakeN8n()
    result = create_automation(PLAN, approve=True, budget=budget, manifest=manifest, client_factory=lambda p: fake)
    assert result["status"] == "blocked"
    assert "budget" in result["summary"]
    assert fake.created == []  # nothing was created


def test_create_failed_on_client_error(tmp_path) -> None:
    class _ExplodingN8n:
        def available(self) -> bool:
            return True

        def unavailable_reason(self) -> str:
            return ""

        def create_workflow(self, workflow: dict) -> dict:
            raise RuntimeError("n8n down")

    budget = BudgetLedger(db_path=str(tmp_path / "budget.db"), cap_usd=10.0)
    manifest = Manifest(path=str(tmp_path / "manifest.jsonl"))
    result = create_automation(PLAN, approve=True, budget=budget, manifest=manifest, client_factory=lambda p: _ExplodingN8n())
    assert result["status"] == "failed"
    assert "RuntimeError" in result["summary"]
    assert manifest.list()[0]["status"] == "failed"


def test_try_parse_plan() -> None:
    assert try_parse_plan("no plan") is None
    assert try_parse_plan('```json\n{"automation": {"provider": "n8n", "name": "x", "description": "y"}}\n```') == {
        "provider": "n8n",
        "name": "x",
        "description": "y",
    }
    assert try_parse_plan('```json\n{"automation": {"provider": "salesforce", "name": "x", "description": "y"}}\n```') is None
