"""Unified task object + event-sourced lifecycle (unified-architecture §27-28).

Every important transition becomes an event; the AuditChain records the
authoritative sequence; the sqlite store is the derived projection. These
tests pin the state machine, the chain mirror, durability across reopen,
restart recovery, the agent handle() integration, and the API surface.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from msb_v3.agent.handle import handle
from msb_v3.api.app import create_app
from msb_v3.core.config import settings
from msb_v3.tasks.events import TaskLifecycleError
from msb_v3.tasks.lifecycle import EventingProvider, TaskLifecycle
from msb_v3.tasks.models import UnifiedTask
from msb_v3.uac.audit_chain import AuditChain


@pytest.fixture()
def chain(tmp_path):
    return AuditChain(db_path=str(tmp_path / "audit.db"), allow_keyless=True)


@pytest.fixture()
def lifecycle(tmp_path, chain):
    return TaskLifecycle(db_path=str(tmp_path / "tasks.db"), chain=chain)


def _task(task_id: str = "t.1") -> UnifiedTask:
    return UnifiedTask(
        task_id=task_id,
        kind="agent.run",
        tenant="wilson-vault",
        session="s",
        intent={"request": "do the thing"},
    )


# --- model ----------------------------------------------------------------


def test_unified_task_round_trip():
    t = _task()
    t.assumptions = ["assumption a"]
    t.plan = {"goal": "g", "source": "template", "tasks": []}
    t.verification = {"verdict": "PASS"}
    t.outcome = {"ok": True, "verdict": "PASS"}
    restored = UnifiedTask.from_dict(t.as_dict())
    assert restored.as_dict() == t.as_dict()
    assert restored.state == "CREATED"


def test_unified_task_merge_sections():
    t = _task()
    t.merge_sections({"outcome": {"ok": True}, "verification": {"verdict": "PASS"}})
    assert t.outcome["ok"] is True
    assert t.verification["verdict"] == "PASS"


# --- lifecycle: create + events -------------------------------------------


def test_create_emits_created_event(lifecycle):
    record = lifecycle.create(_task())
    assert record["state"] == "CREATED"
    events = lifecycle.events("t.1")
    assert [e["event_type"] for e in events] == ["TASK_CREATED"]
    assert events[0]["audit_seq"] is not None  # mirrored to the chain


def test_legal_transition_sequence(lifecycle):
    lifecycle.create(_task())
    lifecycle.transition("t.1", "PLANNED")
    lifecycle.transition("t.1", "EXECUTING")
    lifecycle.transition("t.1", "VERIFYING")
    lifecycle.transition("t.1", "COMPLETED")
    events = [e["event_type"] for e in lifecycle.events("t.1")]
    assert events == [
        "TASK_CREATED",
        "PLAN_CREATED",
        "AGENT_STARTED",
        "VERIFICATION_STARTED",
        "TASK_COMPLETED",
    ]
    assert lifecycle.get("t.1")["state"] == "COMPLETED"


def test_illegal_transition_raises(lifecycle):
    lifecycle.create(_task())
    with pytest.raises(TaskLifecycleError):
        lifecycle.transition("t.1", "COMPLETED")  # CREATED -> COMPLETED not allowed
    assert lifecycle.get("t.1")["state"] == "CREATED"


def test_unknown_event_rejected(lifecycle):
    lifecycle.create(_task())
    with pytest.raises(TaskLifecycleError):
        lifecycle.emit("t.1", "NOT_A_REAL_EVENT", {})


def test_informational_event_does_not_change_state(lifecycle):
    lifecycle.create(_task())
    lifecycle.emit("t.1", "TOOL_EXECUTED", {"tool": "search_vault"})
    assert lifecycle.get("t.1")["state"] == "CREATED"


def test_events_mirrored_to_chain(lifecycle, chain):
    lifecycle.create(_task())
    lifecycle.transition("t.1", "PLANNED")
    lifecycle.emit("t.1", "TOOL_EXECUTED", {"tool": "search_vault"})
    chain_events = [r for r in chain.get_chain(component="tasks")]
    types = [r.event_type for r in chain_events]
    assert types == ["task.TASK_CREATED", "task.PLAN_CREATED", "task.TOOL_EXECUTED"]
    # the chain is the authoritative sequence and verifies
    assert chain.verify_chain()["valid"] is True


def test_durability_across_reopen(tmp_path, chain):
    db = str(tmp_path / "tasks.db")
    first = TaskLifecycle(db_path=db, chain=chain)
    first.create(_task())
    first.transition("t.1", "PLANNED")

    second = TaskLifecycle(db_path=db, chain=chain)
    record = second.get("t.1")
    assert record["state"] == "PLANNED"
    assert [e["event_type"] for e in record["events"]] == ["TASK_CREATED", "PLAN_CREATED"]


def test_recover_incomplete_quarantines(tmp_path, chain):
    lifecycle = TaskLifecycle(db_path=str(tmp_path / "tasks.db"), chain=chain)
    lifecycle.create(_task("in-flight"))
    lifecycle.transition("in-flight", "PLANNED")
    lifecycle.transition("in-flight", "EXECUTING")
    lifecycle.create(_task("done"))
    for state in ("PLANNED", "EXECUTING", "VERIFYING", "COMPLETED"):
        lifecycle.transition("done", state)

    recovered = lifecycle.recover_incomplete()
    assert [r["task_id"] for r in recovered] == ["in-flight"]
    assert lifecycle.get("in-flight")["state"] == "QUARANTINED"
    assert lifecycle.get("done")["state"] == "COMPLETED"  # untouched


# --- EventingProvider -------------------------------------------------------


class _FakeProvider:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def run_tool(self, name, *, task, inputs, session):
        self.calls.append(name)
        if name == "boom":
            raise RuntimeError("exploded")
        return "ok"


def test_eventing_provider_emits_tool_events(lifecycle):
    lifecycle.create(_task("e.1"))
    provider = EventingProvider(_FakeProvider(), lifecycle, "e.1")
    task = type("T", (), {"goal": "g"})()

    async def run():
        return await provider.run_tool("search_vault", task=task, inputs={}, session="s")

    import asyncio

    assert asyncio.run(run()) == "ok"
    types = [e["event_type"] for e in lifecycle.events("e.1")]
    assert "TOOL_REQUESTED" in types
    assert "TOOL_EXECUTED" in types


def test_eventing_provider_marks_mutations(lifecycle):
    lifecycle.create(_task("e.2"))
    provider = EventingProvider(_FakeProvider(), lifecycle, "e.2")
    task = type("T", (), {"goal": "g"})()

    async def run():
        return await provider.run_tool("vault_write", task=task, inputs={}, session="s")

    import asyncio

    asyncio.run(run())
    types = [e["event_type"] for e in lifecycle.events("e.2")]
    assert "MUTATION_COMMITTED" in types


def test_eventing_provider_records_denied_policy(lifecycle):
    lifecycle.create(_task("e.3"))
    provider = EventingProvider(_FakeProvider(), lifecycle, "e.3")
    task = type("T", (), {"goal": "g"})()

    async def run():
        with pytest.raises(RuntimeError):
            await provider.run_tool("boom", task=task, inputs={}, session="s")

    import asyncio

    asyncio.run(run())
    types = [e["event_type"] for e in lifecycle.events("e.3")]
    assert "POLICY_CHECKED" in types
    assert "DENIED" in lifecycle.events("e.3")[-1]["payload"]["decision"]


# --- handle() integration ----------------------------------------------------


class _Resp:
    def __init__(self, text: str) -> None:
        self.text = text
        self.model = "fake"
        self.latency_s = 0.0
        self.tool_calls = []


class _SequenceClient:
    def __init__(self, *texts: str) -> None:
        self._texts = list(texts)

    def generate(self, prompt, *, system=None, tools=None, temperature=0.2, max_tokens=2048):
        text = self._texts.pop(0) if self._texts else "garbage"
        return _Resp(text)


_INTENT_WITH_WRITE = (
    '{"goals": ["research the vault"], "constraints": [], '
    '"permissions": ["read_vault", "write_file"], "privacy": true, "domain": "client-brief"}'
)


class _Audit:
    def __init__(self) -> None:
        self.events: list[tuple] = []

    def append(self, component, event_type, payload) -> None:
        self.events.append((component, event_type, payload))


class _FakeWriteProvider:
    def __init__(self, tmp_path) -> None:
        self._tmp = tmp_path

    async def run_tool(self, name, *, task, inputs, session):
        if name == "search_query":
            return [{"text": "source one", "source": "vault/a.md"}]
        if name == "chat":
            return "The client-ready brief."
        if name == "vault_write":
            (self._tmp / "brief.md").write_text("# Brief\n\nbrief\n")
            return {"path": str(self._tmp / "brief.md"), "heading": "# Brief"}
        raise ValueError(name)


@pytest.mark.asyncio
async def test_handle_drives_lifecycle_to_completed(tmp_path, lifecycle):
    from msb_v3.agent.safety import ActionGate

    result = await handle(
        "research the vault and write a client brief",
        client=_SequenceClient(_INTENT_WITH_WRITE),
        approve=True,
        provider=_FakeWriteProvider(tmp_path),
        gate=ActionGate(audit_chain=_Audit()),
        lifecycle=lifecycle,
    )
    assert result.ok is True
    record = lifecycle.get(result.run_id)
    assert record["state"] == "COMPLETED"
    types = [e["event_type"] for e in record["events"]]
    # the full §28 sequence for a happy path
    assert types[0] == "TASK_CREATED"
    assert "INTENT_INTERPRETED" in types
    assert "PLAN_CREATED" in types
    assert "AGENT_STARTED" in types
    assert "TOOL_EXECUTED" in types
    assert "MUTATION_COMMITTED" in types
    assert "VERIFICATION_STARTED" in types
    assert "VERIFICATION_PASSED" in types
    assert "EVIDENCE_RECORDED" in types
    assert types[-1] == "TASK_COMPLETED"
    # task document carries the §27 outcome + evidence
    body = record["task"]
    assert body["outcome"]["ok"] is True
    assert body["outcome"]["verdict"] == "PASS"
    assert body["evidence"] == [result.deterministic_hash]


@pytest.mark.asyncio
async def test_handle_denied_write_ends_failed(tmp_path, lifecycle):
    from msb_v3.agent.safety import ActionGate

    result = await handle(
        "research the vault and write a client brief",
        client=_SequenceClient(_INTENT_WITH_WRITE),
        approve=False,  # tainted write not pre-approved -> REVIEW gate
        provider=_FakeWriteProvider(tmp_path),
        gate=ActionGate(audit_chain=_Audit()),
        lifecycle=lifecycle,
    )
    assert result.ok is False
    record = lifecycle.get(result.run_id)
    assert record["state"] == "FAILED"
    types = [e["event_type"] for e in record["events"]]
    assert types[-1] == "TASK_FAILED"
    assert "POLICY_CHECKED" in types  # the gate refusal was recorded


# --- API surface -------------------------------------------------------------


def test_agent_task_endpoints(tmp_path, monkeypatch):
    import msb_v3.api.agent as agent_api

    lifecycle = TaskLifecycle(db_path=str(tmp_path / "tasks.db"), chain=AuditChain(db_path=str(tmp_path / "audit.db"), allow_keyless=True))
    lifecycle.create(_task("api.1"))
    for state in ("PLANNED", "EXECUTING", "VERIFYING", "COMPLETED"):
        lifecycle.transition("api.1", state)
    monkeypatch.setattr(agent_api, "_lifecycle", lambda: lifecycle)
    monkeypatch.setattr(settings, "operator_token", "test-operator-token")

    client = TestClient(create_app(), headers={"Authorization": "Bearer test-operator-token"})
    r = client.get("/agent/tasks/api.1")
    assert r.status_code == 200
    assert r.json()["state"] == "COMPLETED"
    assert r.json()["task"]["task_id"] == "api.1"

    r2 = client.get("/agent/tasks/api.1/events")
    assert r2.status_code == 200
    assert [e["event_type"] for e in r2.json()["events"]] == [
        "TASK_CREATED", "PLAN_CREATED", "AGENT_STARTED",
        "VERIFICATION_STARTED", "TASK_COMPLETED",
    ]

    r3 = client.get("/agent/tasks")
    assert r3.status_code == 200
    assert r3.json()["count"] >= 1

    # unknown task -> 404, and the reads are operator-gated (503 when unset)
    assert client.get("/agent/tasks/nope").status_code == 404
    monkeypatch.setattr(settings, "operator_token", "")
    assert client.get("/agent/tasks/api.1").status_code == 503
