"""MSB ↔ Paseo adapter — the six spec operations on the verified MCP surface.

    create_task     -> create_agent (git worktree + initialPrompt, background)
    assign_agent    -> create_agent (provider/model) / update_agent
    send_task       -> send_agent_prompt
    monitor         -> get_agent_status / get_agent_activity / wait_for_agent
    interrupt       -> cancel_agent (abort run, keep alive) / kill_agent
    retrieve_result -> get_agent_status snapshot

``drive_run`` is the governed end-to-end primitive MSB uses to hand a task to
a Paseo worker: create the agent in an isolated worktree, block on
``wait_for_agent``, and when the worker raises a permission request, park the
run on a durable operator-gated Vesta approval (see ``permissions.py``) —
the request never flows back to the worker unattended. A denial or decision
timeout interrupts the run; the task fails with evidence, never silently
completes.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from msb_v3.agent.paseo.client import PaseoMcpClient, PaseoMcpError
from msb_v3.agent.paseo.permissions import PaseoPermissionBroker
from msb_v3.core.config import settings

logger = logging.getLogger(__name__)

# Paseo provider ids (AgentProviderEnum in the daemon's provider-registry).
PASEO_PROVIDERS = ("claude", "codex", "opencode")

# Activity sampling while a wait is in flight: how often to poll the daemon's
# curated activity feed, and how many recent entries to fetch per sample.
_SAMPLE_INTERVAL_S = 3.0
_ACTIVITY_LIMIT = 8
_ACTIVITY_TIMEOUT_S = 5.0


def _slug(value: str) -> str:
    """Worktree branch names: lowercase alphanumerics + hyphens only."""
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (cleaned or "msb-task")[:60]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PaseoAdapter:
    """Thin, honest wrapper over the daemon's agent-management MCP tools."""

    def __init__(
        self,
        client: Optional[PaseoMcpClient] = None,
        *,
        broker: Optional[PaseoPermissionBroker] = None,
        approvals_store: Any = None,
        wait_timeout_s: float = 300.0,
        run_timeout_s: Optional[float] = None,
        sample_interval_s: float = _SAMPLE_INTERVAL_S,
    ) -> None:
        self.client = client or PaseoMcpClient(settings.paseo_url)
        self.run_timeout_s = run_timeout_s or settings.paseo_run_timeout_s
        self.wait_timeout_s = wait_timeout_s
        self.sample_interval_s = sample_interval_s
        if broker is None:
            broker = PaseoPermissionBroker(
                approvals=approvals_store,
                ttl_s=settings.paseo_permission_ttl_s,
            )
        self.broker = broker

    async def _call(self, name: str, arguments: Dict[str, Any], *, timeout_s: Optional[float] = None) -> Any:
        return await self.client.call_tool(name, arguments, timeout_s=timeout_s)

    # --- the six spec operations -------------------------------------------------

    async def create_task(
        self,
        *,
        cwd: str,
        title: str,
        task: Optional[str] = None,
        provider: str = "claude",
        model: Optional[str] = None,
        mode: Optional[str] = None,
        worktree_name: Optional[str] = None,
        base_branch: Optional[str] = None,
        background: bool = True,
    ) -> Dict[str, Any]:
        """Spec ``create_task``: a Paseo agent in an isolated git worktree.

        ``worktree_name`` is required for code tasks — the worktree is the
        isolation boundary (spec §7). Returns {agent_id, status, cwd}.
        """
        if provider not in PASEO_PROVIDERS:
            raise PaseoMcpError(f"unknown paseo provider: {provider} (expected one of {PASEO_PROVIDERS})")
        args: Dict[str, Any] = {
            "cwd": cwd,
            "title": (title or "msb task")[:60],
            "provider": provider,
            "background": bool(background),
        }
        if task:
            args["initialPrompt"] = task
        if model:
            args["model"] = model
        if mode:
            args["mode"] = mode
        if worktree_name:
            args["worktreeName"] = _slug(worktree_name)
            args["baseBranch"] = base_branch or "main"
        result = await self._call("create_agent", args)
        return {
            "agent_id": result.get("agentId"),
            "status": result.get("status"),
            "cwd": result.get("cwd"),
            "permission": result.get("permission"),
        }

    async def assign_agent(
        self,
        *,
        cwd: str,
        title: str,
        provider: str = "claude",
        model: Optional[str] = None,
        worktree_name: Optional[str] = None,
        base_branch: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Spec ``assign_agent``: create an idle worker with a chosen provider
        and model (from ``list_models``/``list_providers``)."""
        return await self.create_task(
            cwd=cwd, title=title, provider=provider, model=model,
            worktree_name=worktree_name, base_branch=base_branch, background=True,
        )

    async def send_task(
        self,
        agent_id: str,
        prompt: str,
        *,
        session_mode: Optional[str] = None,
        background: bool = True,
    ) -> Dict[str, Any]:
        """Spec ``send_task``: give a running agent a task."""
        args: Dict[str, Any] = {"agentId": agent_id, "prompt": prompt, "background": bool(background)}
        if session_mode:
            args["sessionMode"] = session_mode
        result = await self._call("send_agent_prompt", args)
        return {
            "success": bool(result.get("success", True)),
            "status": result.get("status"),
            "permission": result.get("permission"),
            "last_message": result.get("lastMessage"),
        }

    async def monitor(self, agent_id: str) -> Dict[str, Any]:
        """Spec ``monitor``: current snapshot (status, mode, pending permissions)."""
        result = await self._call("get_agent_status", {"agentId": agent_id})
        snapshot = result.get("snapshot") or {}
        permissions = snapshot.get("pendingPermissions") or []
        return {
            "status": result.get("status") or snapshot.get("lifecycle"),
            "cwd": snapshot.get("cwd"),
            "mode": snapshot.get("currentModeId"),
            "last_message": snapshot.get("lastMessage"),
            "pending_permissions": len(permissions),
        }

    async def activity(self, agent_id: str, *, limit: Optional[int] = None) -> Dict[str, Any]:
        """Rich status: the daemon's curated agent activity timeline
        (``get_agent_activity``) — recent updates as readable content."""
        args: Dict[str, Any] = {"agentId": agent_id}
        if limit is not None:
            args["limit"] = limit
        result = await self._call("get_agent_activity", args, timeout_s=_ACTIVITY_TIMEOUT_S)
        return {
            "update_count": result.get("updateCount", 0),
            "mode": result.get("currentModeId"),
            "content": str(result.get("content") or ""),
        }

    async def wait_for_agent(self, agent_id: str, *, timeout_s: Optional[float] = None) -> Dict[str, Any]:
        """Block until the agent requests permission or the run completes."""
        result = await self._call("wait_for_agent", {"agentId": agent_id}, timeout_s=timeout_s)
        return {
            "status": result.get("status"),
            "permission": result.get("permission"),
            "last_message": result.get("lastMessage"),
        }

    async def interrupt(self, agent_id: str, *, kill: bool = False) -> bool:
        """Spec ``interrupt``: abort the current run (keep alive) or kill."""
        tool = "kill_agent" if kill else "cancel_agent"
        result = await self._call(tool, {"agentId": agent_id})
        return bool(result.get("success", False))

    async def retrieve_result(self, agent_id: str) -> Dict[str, Any]:
        """Spec ``retrieve_result``: the final snapshot (status, cwd, message).

        The full worktree diff vs the base branch is available via the
        daemon's checkout-diff manager; v1 returns the snapshot the daemon
        already keeps, so MSB's verification has concrete output to check.
        """
        result = await self._call("get_agent_status", {"agentId": agent_id})
        snapshot = result.get("snapshot") or {}
        return {
            "status": result.get("status") or snapshot.get("lifecycle"),
            "cwd": snapshot.get("cwd"),
            "mode": snapshot.get("currentModeId"),
            "last_message": snapshot.get("lastMessage"),
            "pending_permissions": len(snapshot.get("pendingPermissions") or []),
        }

    # --- permission forwarding (performed by the waiting run) --------------------

    async def _forward_permission(self, agent_id: str, request_id: str, approved: bool, message: str = "") -> None:
        response: Dict[str, Any] = {"behavior": "allow" if approved else "deny"}
        if not approved:
            response["interrupt"] = True
            if message:
                response["message"] = message
        await self._call(
            "respond_to_permission",
            {"agentId": agent_id, "requestId": request_id, "response": response},
        )
        logger.info("paseo permission %s forwarded (%s)", request_id, "allow" if approved else "deny")

    # --- governed end-to-end run ---------------------------------------------------

    async def _wait_sampling_observations(
        self,
        agent_id: str,
        timeout_s: float,
        observations: List[Dict[str, Any]],
        on_observation: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Block on the daemon while sampling the agent's activity feed.

        ``wait_for_agent`` blocks server-side until a permission request or
        run completion — that coroutine runs as a task while this method
        polls ``get_agent_activity`` every few seconds, appending each new
        curated update to ``observations`` and invoking ``on_observation``
        (the lifecycle sink). Sampling is best-effort: a failed activity
        poll is skipped; the wait itself governs.
        """
        wait_task = asyncio.create_task(self.wait_for_agent(agent_id, timeout_s=timeout_s))
        last_count: Optional[int] = None
        try:
            while not wait_task.done():
                try:
                    act = await self.activity(agent_id, limit=_ACTIVITY_LIMIT)
                    count = act.get("update_count", 0)
                    if count != last_count and act.get("content"):
                        last_count = count
                        sample = {
                            "source": "paseo.activity",
                            "observed_at": _now(),
                            "update_count": count,
                            "mode": act.get("mode"),
                            "content": act["content"],
                        }
                        observations.append(sample)
                        if on_observation is not None:
                            try:
                                await on_observation(sample)
                            except Exception:  # noqa: BLE001 — sink must never break the run
                                logger.warning("observation sink failed for %s", agent_id)
                except PaseoMcpError:
                    pass  # activity is bonus; the wait result is authoritative
                try:
                    await asyncio.wait_for(asyncio.shield(wait_task), timeout=self.sample_interval_s)
                except asyncio.TimeoutError:
                    continue
        finally:
            if not wait_task.done():
                wait_task.cancel()
        return await wait_task

    async def drive_run(
        self,
        *,
        goal: str,
        cwd: str,
        base_branch: str = "main",
        provider: str = "claude",
        model: Optional[str] = None,
        task_id: str = "",
        timeout_s: Optional[float] = None,
        on_observation: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Run a task on a Paseo worker end-to-end, governed.

        Creates the agent in an isolated worktree, blocks on the daemon,
        streams the agent's activity feed into ``observations`` (a list in
        the result; ``on_observation`` gets each sample for the lifecycle
        sink), and parks every permission request on an operator-gated
        approval. Returns a dict with ok/agent_id/status/last_message/
        error/observations. Raises PaseoMcpError only for transport
        failures (daemon down mid-run).
        """
        observations: List[Dict[str, Any]] = []
        deadline = time.monotonic() + (timeout_s or self.run_timeout_s)
        created = await self.create_task(
            cwd=cwd,
            title=goal[:60],
            task=goal,
            provider=provider,
            model=model,
            worktree_name=f"msb-{task_id or _slug(goal[:24])}",
            base_branch=base_branch,
            background=True,
        )
        agent_id = created.get("agent_id")
        if not agent_id:
            return {"ok": False, "agent_id": "", "status": "error", "error": f"create_agent returned no agentId: {created}"}
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    await self.interrupt(agent_id)
                    return {
                        "ok": False, "agent_id": agent_id, "status": "timed_out",
                        "error": f"paseo run timed out after {timeout_s or self.run_timeout_s}s",
                    }
                result = await self._wait_sampling_observations(
                    agent_id, min(remaining, self.wait_timeout_s), observations, on_observation
                )
                permission = result.get("permission")
                if permission:
                    request_id = permission.get("id")
                    approval = await self.broker.register(agent_id, task_id or agent_id, permission)
                    decision = await self.broker.wait_for_decision(
                        approval["approval_id"], min(self.broker.ttl_s, remaining)
                    )
                    if decision is None or decision.get("status") != "APPROVED":
                        # Timeout: the daemon run is still parked — stop it.
                        if decision is None:
                            await self.interrupt(agent_id)
                        reason = str(decision.get("reason")) if decision else "permission decision timed out"
                        # The waiting run is the single forwarder: deny with
                        # interrupt so the worker stops, never continues.
                        try:
                            await self._forward_permission(agent_id, request_id, False, reason)
                        except Exception as exc:  # noqa: BLE001 — backstop below
                            logger.warning("deny forward failed (%s); interrupting as backstop", exc)
                            try:
                                await self.interrupt(agent_id)
                            except Exception:  # noqa: BLE001
                                pass
                        return {
                            "ok": False, "agent_id": agent_id, "status": "denied",
                            "error": f"permission {permission.get('name', request_id)} denied: {reason}",
                        }
                    # Approved — the run forwards the allow and resumes.
                    await self._forward_permission(agent_id, request_id, True, "")
                    continue
                status = result.get("status")
                if status in ("error", "closed"):
                    return {
                        "ok": False, "agent_id": agent_id, "status": status,
                        "error": result.get("last_message") or f"paseo agent ended with status {status}",
                    }
                if status == "running":
                    continue  # guard: wait_for_agent should block, never spin
                # idle + no permission = run complete.
                final = await self.retrieve_result(agent_id)
                final["observations"] = observations
                return {
                    "ok": True,
                    "agent_id": agent_id,
                    "status": final.get("status") or "idle",
                    "last_message": result.get("last_message") or final.get("last_message") or "",
                    "extra": final,
                }
        except Exception:
            # Transport failure mid-run: the worker may be orphaned. Stop it
            # best-effort so nothing keeps running unattended, then re-raise.
            try:
                await self.interrupt(agent_id)
            except Exception:  # noqa: BLE001
                pass
            raise
