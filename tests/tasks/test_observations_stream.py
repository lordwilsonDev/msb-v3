"""Observation stream — bus unit tests + SSE endpoint tests.

The bus is the live channel: per-task subscriber queues the observation
sink publishes to. The SSE endpoint replays the task's recorded
observations, then streams new ones live, closing with `event: done` on a
terminal state. The live path is exercised in-loop (no TestClient
threading races); the HTTP surface is tested for replay, 404, and auth.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from msb_v3.api.app import create_app
from msb_v3.core.config import settings
from msb_v3.tasks import observations
from msb_v3.tasks.lifecycle import TaskLifecycle
from msb_v3.tasks.models import UnifiedTask
from msb_v3.uac.audit_chain import AuditChain

# --- bus --------------------------------------------------------------------


def test_bus_publish_reaches_subscriber():
    async def run():
        queue = observations.subscribe("t1")
        try:
            await observations.publish("t1", {"update_count": 1, "content": "hi"})
            sample = await asyncio.wait_for(queue.get(), timeout=1.0)
            assert sample["update_count"] == 1
        finally:
            observations.unsubscribe("t1", queue)

    asyncio.run(run())


def test_bus_unsubscribe_stops_delivery():
    async def run():
        queue = observations.subscribe("t2")
        observations.unsubscribe("t2", queue)
        await observations.publish("t2", {"update_count": 1})
        assert queue.empty()

    asyncio.run(run())


def test_bus_full_queue_drops_oldest():
    async def run():
        queue = observations.subscribe("t3")
        try:
            for i in range(observations._QUEUE_MAX + 5):
                await observations.publish("t3", {"update_count": i})
            # newest 100 of 105 survive; the oldest five are dropped
            assert queue.qsize() == observations._QUEUE_MAX
            first = queue.get_nowait()
            assert first["update_count"] == 5
        finally:
            observations.unsubscribe("t3", queue)

    asyncio.run(run())


# --- SSE stream (in-loop live path) ------------------------------------------


def test_observation_stream_live_flow(tmp_path):
    """Replay + live samples + terminal done, driven in one event loop."""

    async def run():
        import msb_v3.api.agent as agent_api
        from msb_v3.api.agent import _observation_stream

        # tight poll so the terminal-done check fires fast
        orig_poll = agent_api._STREAM_POLL_S
        agent_api._STREAM_POLL_S = 0.05
        try:
            chain = AuditChain(db_path=str(tmp_path / "audit.db"), allow_keyless=True)
            lifecycle = TaskLifecycle(db_path=str(tmp_path / "tasks.db"), chain=chain)
            # the generator re-checks terminal state through _lifecycle();
            # point it at the same lifecycle the initial dict came from.
            agent_api._lifecycle = lambda: lifecycle
            lifecycle.create(UnifiedTask(task_id="t-live", state="CREATED"))
            for state in ("PLANNED", "EXECUTING"):
                lifecycle.transition("t-live", state)

            # Replay should include pre-recorded observations. The endpoint
            # passes a flat {observations, state} slice of the lifecycle
            # record; mirror that shape here.
            lifecycle.update("t-live", {"observations": [{"update_count": 1, "content": "prior"}]})
            rec = lifecycle.get("t-live")
            gen = _observation_stream(
                "t-live",
                {"observations": rec["task"].get("observations", []), "state": rec["state"]},
            )
            events: list[str] = []

            async def collect():
                async for chunk in gen:
                    events.append(chunk)

            collector = asyncio.create_task(collect())
            await asyncio.sleep(0.05)  # let the generator subscribe
            assert observations.subscriber_count("t-live") == 1
            await observations.publish("t-live", {"update_count": 2, "content": "live one"})
            await observations.publish("t-live", {"update_count": 3, "content": "live two"})
            # publish/transition never yield internally, so the generator
            # could otherwise hit the terminal state before consuming the
            # queued samples. Poll until the live samples are observed, then
            # end the run. (chunks are full SSE frames, so match on the
            # event line like the assertions below do)
            def _obs_count():
                return sum(1 for c in events if c.split("\n")[0] == "event: observation")

            for _ in range(100):
                if _obs_count() >= 3:
                    break
                await asyncio.sleep(0.02)
            assert _obs_count() == 3
            lifecycle.transition("t-live", "VERIFYING")
            lifecycle.transition("t-live", "COMPLETED")
            await asyncio.wait_for(collector, timeout=5.0)
            assert observations.subscriber_count("t-live") == 0  # cleaned up

            # replay (1) + live (2) + done
            kinds = [c.split("\n")[0] for c in events]
            assert kinds.count("event: observation") == 3
            assert "event: done" in kinds
            blob = "".join(events)
            assert "prior" in blob and "live one" in blob and "live two" in blob
        finally:
            agent_api._STREAM_POLL_S = orig_poll

    asyncio.run(run())


# --- HTTP surface ------------------------------------------------------------


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "operator_token", "test-operator-token")
    chain = AuditChain(db_path=str(tmp_path / "audit.db"), allow_keyless=True)
    lifecycle = TaskLifecycle(db_path=str(tmp_path / "tasks.db"), chain=chain)
    import msb_v3.api.agent as agent_api

    monkeypatch.setattr(agent_api, "_lifecycle", lambda: lifecycle)
    # keep the endpoint test fast: poll fires immediately, terminal state
    # ends the stream instead of the 2s default.
    monkeypatch.setattr(agent_api, "_STREAM_POLL_S", 0.1)
    return TestClient(create_app(), headers={"Authorization": "Bearer test-operator-token"})


def _completed_task_with_observations(lifecycle, task_id="t1"):
    lifecycle.create(UnifiedTask(task_id=task_id, state="CREATED"))
    for state in ("PLANNED", "EXECUTING", "VERIFYING", "COMPLETED"):
        lifecycle.transition(task_id, state)
    lifecycle.update(task_id, {"observations": [{"update_count": 1, "content": "first"}, {"update_count": 2, "content": "second"}]})
    return task_id


def _parse_sse(text: str) -> list[tuple]:
    """Parse SSE text into (event, data) pairs; heartbeat comments skipped."""
    out = []
    current = {}
    for line in text.splitlines():
        if line.startswith(":") or not line:
            if current:
                out.append((current.get("event", "message"), current.get("data", "")))
                current = {}
            continue
        key, _, value = line.partition(":")
        current[key.strip()] = value.strip()
    if current:
        out.append((current.get("event", "message"), current.get("data", "")))
    return out


def test_stream_replays_then_closes(client, tmp_path, monkeypatch):
    import msb_v3.api.agent as agent_api

    lifecycle = agent_api._lifecycle()
    _completed_task_with_observations(lifecycle)

    text = ""
    with client.stream("GET", "/agent/tasks/t1/observations/stream") as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        for line in resp.iter_lines():
            text += line + "\n"

    events = _parse_sse(text)
    obs = [d for e, d in events if e == "observation"]
    assert len(obs) == 2
    assert "first" in obs[0] and "second" in obs[1]
    assert any(e == "done" for e, _ in events)


def test_stream_unknown_task_404(client):
    assert client.get("/agent/tasks/ghost/observations/stream").status_code == 404


def test_stream_requires_token(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "operator_token", "test-operator-token")
    no_auth = TestClient(create_app())
    assert no_auth.get("/agent/tasks/t1/observations/stream").status_code == 401
    assert (
        no_auth.get("/agent/tasks/t1/observations/stream?token=wrong").status_code == 401
    )


def test_stream_accepts_query_token(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "operator_token", "test-operator-token")
    chain = AuditChain(db_path=str(tmp_path / "audit.db"), allow_keyless=True)
    lifecycle = TaskLifecycle(db_path=str(tmp_path / "tasks.db"), chain=chain)
    import msb_v3.api.agent as agent_api

    monkeypatch.setattr(agent_api, "_lifecycle", lambda: lifecycle)
    monkeypatch.setattr(agent_api, "_STREAM_POLL_S", 0.05)
    _completed_task_with_observations(lifecycle, task_id="t2")

    # EventSource cannot set headers — the query token is the supported path.
    with TestClient(create_app()).stream(
        "GET", "/agent/tasks/t2/observations/stream?token=test-operator-token"
    ) as resp:
        assert resp.status_code == 200
        for _ in resp.iter_lines():
            pass  # drains; terminal state closes the stream
