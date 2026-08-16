"""PaseoAdapter tests — the six spec operations and the governed drive_run
permission flow (docs/paseo-adapter-v1.md §3-4).

drive_run is the heart: create in an isolated worktree, block on the daemon,
and park every permission request on an operator-gated Vesta approval. These
tests drive the full loop against the fake daemon and assert the exact
forwarded responses (allow / deny+interrupt) and the interruption paths.
"""

from __future__ import annotations

import asyncio

import pytest

from msb_v3.agent.paseo import PaseoAdapter, PaseoMcpError, PaseoPermissionBroker
from msb_v3.agent.paseo.permissions import parse_bind
from msb_v3.vesta.approvals import VestaApprovalStore


def _adapter(daemon, http_client, tmp_path, **kwargs):
    return PaseoAdapter(
        client=http_client(),
        approvals_store=VestaApprovalStore(db_path=str(tmp_path / "approvals.db")),
        wait_timeout_s=kwargs.pop("wait_timeout_s", 5.0),
        **kwargs,
    )


async def _wait_until(predicate, timeout_s=2.0):
    import time

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return False


def test_create_task_creates_worktree_agent(daemon, http_client, tmp_path):
    async def run():
        adapter = _adapter(daemon, http_client, tmp_path)
        result = await adapter.create_task(
            cwd="/tmp/repo", title="Implement auth", task="add login",
            provider="claude", worktree_name="msb-auth", base_branch="main",
        )
        assert result["agent_id"] == "agent-1"
        assert result["status"] == "idle"
        name, args = daemon.tool_calls[0]
        assert name == "create_agent"
        assert args["cwd"] == "/tmp/repo"
        assert args["worktreeName"] == "msb-auth"
        assert args["baseBranch"] == "main"
        assert args["initialPrompt"] == "add login"
        assert args["provider"] == "claude"

    asyncio.run(run())


def test_unknown_provider_rejected(daemon, http_client, tmp_path):
    async def run():
        adapter = _adapter(daemon, http_client, tmp_path)
        with pytest.raises(PaseoMcpError, match="unknown paseo provider"):
            await adapter.create_task(cwd="/tmp", title="x", provider="gemini")

    asyncio.run(run())


def test_send_task_and_interrupt(daemon, http_client, tmp_path):
    async def run():
        adapter = _adapter(daemon, http_client, tmp_path)
        sent = await adapter.send_task("agent-1", "continue the work")
        assert sent["success"] is True
        assert daemon.tool_calls[-1][0] == "send_agent_prompt"
        assert await adapter.interrupt("agent-1") is True
        assert daemon.cancelled == ["agent-1"]
        assert await adapter.interrupt("agent-1", kill=True) is True
        assert daemon.tool_calls[-1][0] == "kill_agent"

    asyncio.run(run())


def test_monitor_and_retrieve_result(daemon, http_client, tmp_path):
    daemon.agents["agent-1"] = {"args": {"cwd": "/tmp/repo"}, "lifecycle": "idle", "lastMessage": "done", "pending": []}

    async def run():
        adapter = _adapter(daemon, http_client, tmp_path)
        mon = await adapter.monitor("agent-1")
        assert mon["status"] == "idle"
        assert mon["cwd"] == "/tmp/repo"
        res = await adapter.retrieve_result("agent-1")
        assert res["last_message"] == "done"

    asyncio.run(run())


def _queue_permission_then_complete(daemon, agent_id="agent-1", permission=None, complete="task complete"):
    permission = permission or {"id": "perm-1", "provider": "claude", "name": "Write file", "kind": "tool"}
    daemon.wait_queue[agent_id] = [
        {"status": "idle", "permission": permission, "lastMessage": None},
        {"status": "idle", "permission": None, "lastMessage": complete},
    ]


def test_drive_run_approves_permission_and_completes(daemon, http_client, tmp_path):
    _queue_permission_then_complete(daemon)

    async def run():
        adapter = _adapter(daemon, http_client, tmp_path)
        run_task = asyncio.create_task(
            adapter.drive_run(goal="implement auth", cwd=str(tmp_path), base_branch="main", task_id="t1", timeout_s=30)
        )
        assert await _wait_until(lambda: len(adapter.broker.pending()) == 1)
        approval = adapter.broker.pending()[0]
        assert parse_bind(approval["bind_id"]) == ("agent-1", "perm-1")
        await adapter.broker.decide(approval["approval_id"], "operator", True)
        result = await run_task
        assert result["ok"] is True
        assert result["agent_id"] == "agent-1"
        assert result["last_message"] == "task complete"
        # the waiting run forwarded the allow exactly once (single forwarder)
        assert daemon.responded == [("agent-1", "perm-1", {"behavior": "allow"})]

    asyncio.run(run())


def test_drive_run_denied_permission_interrupts(daemon, http_client, tmp_path):
    _queue_permission_then_complete(daemon)

    async def run():
        adapter = _adapter(daemon, http_client, tmp_path)
        run_task = asyncio.create_task(
            adapter.drive_run(goal="implement auth", cwd=str(tmp_path), task_id="t1", timeout_s=30)
        )
        assert await _wait_until(lambda: len(adapter.broker.pending()) == 1)
        approval = adapter.broker.pending()[0]
        await adapter.broker.decide(approval["approval_id"], "operator", False, message="no write access")
        result = await run_task
        assert result["ok"] is False
        assert result["status"] == "denied"
        assert "denied" in result["error"]
        # the waiting run forwarded deny with interrupt: worker stops, never continues
        assert daemon.responded == [("agent-1", "perm-1", {"behavior": "deny", "interrupt": True, "message": "no write access"})]

    asyncio.run(run())


def test_drive_run_permission_timeout_stops_agent(daemon, http_client, tmp_path):
    _queue_permission_then_complete(daemon)
    broker = PaseoPermissionBroker(
        ttl_s=1,
        approvals=VestaApprovalStore(db_path=str(tmp_path / "approvals.db")),
    )  # no forward needed — never decided

    async def run():
        adapter = PaseoAdapter(
            client=http_client(),
            broker=broker,
            approvals_store=VestaApprovalStore(db_path=str(tmp_path / "approvals.db")),
            wait_timeout_s=5.0,
        )
        result = await adapter.drive_run(goal="implement auth", cwd=str(tmp_path), task_id="t1", timeout_s=30)
        assert result["ok"] is False
        assert "timed out" in result["error"]
        assert daemon.cancelled == ["agent-1"]  # parked run stopped, not left running

    asyncio.run(run())


def test_cross_instance_decision_wakes_waiter(daemon, http_client, tmp_path):
    """The API creates a fresh broker per request — its decide() must still
    wake a drive_run parked on a different broker instance (module-level
    wait registry, the production deployment shape)."""
    _queue_permission_then_complete(daemon)
    store = VestaApprovalStore(db_path=str(tmp_path / "approvals.db"))

    async def run():
        adapter = PaseoAdapter(
            client=http_client(),
            approvals_store=store,
            wait_timeout_s=5.0,
        )
        run_task = asyncio.create_task(
            adapter.drive_run(goal="implement auth", cwd=str(tmp_path), task_id="t1", timeout_s=30)
        )
        assert await _wait_until(lambda: len(adapter.broker.pending()) == 1)
        approval = adapter.broker.pending()[0]
        # A *different* broker instance (as an API request would construct)
        # decides — no shared instance, no shared future object.
        other = PaseoPermissionBroker(approvals=store)
        await other.decide(approval["approval_id"], "operator", True)
        result = await asyncio.wait_for(run_task, timeout=10)
        assert result["ok"] is True
        # the waiting run (single forwarder) applied the decision to the daemon
        assert daemon.responded == [("agent-1", "perm-1", {"behavior": "allow"})]

    asyncio.run(run())


def test_activity_returns_curated_timeline(daemon, http_client, tmp_path):
    daemon.activity_queue["agent-1"] = [
        {"updateCount": 2, "currentModeId": "default", "content": "Showing 2 activities\n\n- started"}
    ]

    async def run():
        adapter = _adapter(daemon, http_client, tmp_path)
        act = await adapter.activity("agent-1", limit=5)
        assert act["update_count"] == 2
        assert "started" in act["content"]
        assert daemon.tool_calls[-1][0] == "get_agent_activity"
        assert daemon.tool_calls[-1][1] == {"agentId": "agent-1", "limit": 5}

    asyncio.run(run())


def test_drive_run_streams_observations(daemon, http_client, tmp_path):
    """While the daemon blocks on wait_for_agent, activity samples stream
    into the observations list and the on_observation sink."""
    daemon.wait_block_s = 0.15
    daemon.wait_queue["agent-1"] = [{"status": "idle", "permission": None, "lastMessage": "done"}]
    daemon.activity_queue["agent-1"] = [
        {"updateCount": 1, "currentModeId": "default", "content": "planning the change"},
        {"updateCount": 4, "currentModeId": "default", "content": "editing hello.txt"},
    ]
    sunk: list = []

    async def run():
        adapter = _adapter(daemon, http_client, tmp_path, sample_interval_s=0.01)

        async def sink(sample):
            sunk.append(sample)

        result = await adapter.drive_run(
            goal="small task", cwd=str(tmp_path), task_id="t9", timeout_s=30, on_observation=sink
        )
        assert result["ok"] is True
        obs = result["extra"]["observations"]
        assert len(obs) >= 2
        assert {o["update_count"] for o in obs} == {1, 4}
        assert all(o["source"] == "paseo.activity" and o["content"] for o in obs)
        assert len(sunk) == len(obs)  # the sink saw each sample
        assert sunk[0]["update_count"] == 1

    asyncio.run(run())


def test_handle_delegation_streams_observations_into_task(daemon, http_client, tmp_path):
    """The full production path: handle() -> PaseoAgentProvider -> drive_run
    streams OBSERVATION_RECORDED events + the §27 observations section into
    the unified task."""
    daemon.wait_block_s = 0.15
    daemon.wait_queue["agent-1"] = [{"status": "idle", "permission": None, "lastMessage": "done"}]
    daemon.activity_queue["agent-1"] = [
        {"updateCount": 1, "currentModeId": "default", "content": "thinking about the task"},
        {"updateCount": 3, "currentModeId": "default", "content": "wrote the code"},
    ]

    async def run():
        from msb_v3.agent.handle import handle
        from msb_v3.agent.identity import AgentIdentity, AgentRegistry
        from msb_v3.agent.providers import PaseoAgentProvider, ProviderRegistry
        from msb_v3.fabric.context_engine import ContextPackage
        from msb_v3.tasks.lifecycle import TaskLifecycle
        from msb_v3.uac.audit_chain import AuditChain

        class _FakeContextEngine:
            def compose(self, task, **kwargs):
                return ContextPackage(text="ctx: %s" % task, budget_tokens=100, total_tokens=4, naive_tokens=20)

        class _FakeMemoryFabric:
            def store_memory(self, *args, **kwargs):
                return type("Item", (), {"memory_id": "mem-x"})()

            def consolidate(self, tenant="default", *, by="system"):
                return {"tenant": tenant, "merged": 0, "deprecations": [], "decayed": 0, "kept": 1}

        registry = AgentRegistry(db_path=str(tmp_path / "agents.db"))
        registry.register(
            AgentIdentity(agent_id="p1", name="p", kind="paseo", provider_id="paseo.claude")
        )
        adapter = PaseoAdapter(
            client=http_client(),
            approvals_store=VestaApprovalStore(db_path=str(tmp_path / "approvals.db")),
            sample_interval_s=0.01,
        )
        providers = ProviderRegistry((PaseoAgentProvider("claude", adapter=adapter, available=True),))
        chain = AuditChain(db_path=str(tmp_path / "audit.db"), allow_keyless=True)
        lifecycle = TaskLifecycle(db_path=str(tmp_path / "tasks.db"), chain=chain)
        result = await handle(
            "do the thing",
            agent_id="p1",
            registry=registry,
            providers=providers,
            lifecycle=lifecycle,
            repo="/tmp/repo",
            context_engine=_FakeContextEngine(),
            memory_fabric=_FakeMemoryFabric(),
        )
        assert result.ok is True
        record = lifecycle.get(result.run_id)
        types = [e["event_type"] for e in record["events"]]
        assert "OBSERVATION_RECORDED" in types
        obs = record["task"]["observations"]
        assert len(obs) >= 2
        assert {o["update_count"] for o in obs} == {1, 3}
        assert all(o["source"] == "paseo.activity" for o in obs)

    asyncio.run(run())


def test_drive_run_activity_failure_is_best_effort(daemon, http_client, tmp_path):
    """A failed activity poll must never break the run — the wait governs."""
    daemon.wait_block_s = 0.1
    daemon.wait_queue["agent-1"] = [{"status": "idle", "permission": None, "lastMessage": "done"}]
    daemon.activity_fail = True

    async def run():
        adapter = _adapter(daemon, http_client, tmp_path, sample_interval_s=0.01)
        result = await adapter.drive_run(goal="small task", cwd=str(tmp_path), task_id="t10", timeout_s=30)
        assert result["ok"] is True
        assert result["extra"]["observations"] == []  # nothing sampled, run intact

    asyncio.run(run())


def test_drive_run_completes_without_permission(daemon, http_client, tmp_path):
    daemon.wait_queue["agent-1"] = [{"status": "idle", "permission": None, "lastMessage": "done in one shot"}]

    async def run():
        adapter = _adapter(daemon, http_client, tmp_path)
        result = await adapter.drive_run(goal="small task", cwd=str(tmp_path), task_id="t2", timeout_s=30)
        assert result["ok"] is True
        assert result["last_message"] == "done in one shot"
        assert daemon.responded == []  # no permission, nothing forwarded

    asyncio.run(run())


def test_drive_run_timeout_interrupts_orphaned_worker(daemon, http_client, tmp_path):
    # daemon never completes: wait_for_agent always returns "running"
    daemon.always_running = True

    async def run():
        adapter = _adapter(daemon, http_client, tmp_path, wait_timeout_s=1.0)
        result = await adapter.drive_run(goal="never ends", cwd=str(tmp_path), task_id="t3", timeout_s=2.0)
        assert result["ok"] is False
        assert "timed out" in result["error"]
        assert "agent-1" in daemon.cancelled

    asyncio.run(run())
