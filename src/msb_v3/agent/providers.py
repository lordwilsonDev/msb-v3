"""Agent provider abstraction (unified-architecture §7, §31 item 14).

A provider is how MSB gets work done by a worker — the seam Paseo agents
plug into. Two kinds today:

    local   LocalAgentProvider  — the existing sovereign slice (intent ->
                                  DAG -> gated tools -> verify), executed in
                                  this process.
    cli     CliAgentProvider    — an external CLI agent (Claude Code, Codex,
                                  OpenCode) run as a bounded subprocess in an
                                  isolated worktree: MSB sends the task, the
                                  agent works, MSB retrieves the result.

Every provider declares its capabilities and max risk tier; ``ProviderRegistry``
selects deterministically (available + capable + within risk tier). Workers are
never sovereign authorities — they hold only the capabilities their registered
identity was granted (see ``agent/identity.py``).

Safety note (stated plainly): a CLI agent runs with the operator's user
account — worktree isolation bounds where it *should* write, but is NOT a
sandbox. That is exactly why CLI providers are HIGH risk and require
operator registration with scoped capabilities.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from msb_v3.core.config import settings

logger = logging.getLogger(__name__)

# Max output/artifact bytes captured from a CLI worker (bounded evidence).
_MAX_OUTPUT_BYTES = 200_000
_MAX_ARTIFACTS = 20


@dataclass(frozen=True)
class ProviderSpec:
    provider_id: str  # "local.slice" | "cli.claude" | "cli.codex" | "cli.opencode"
    display_name: str
    kind: str  # "local" | "cli"
    command: Tuple[str, ...] = ()  # cli only: the executable + fixed args
    capabilities: Tuple[str, ...] = ()
    max_risk_tier: int = 2
    timeout_s: float = 120.0


@dataclass(frozen=True)
class ProviderResult:
    ok: bool
    output: str = ""
    artifacts: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    duration_s: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "output_head": self.output[:500],
            "output_len": len(self.output),
            "artifacts": self.artifacts,
            "error": self.error,
            "duration_s": self.duration_s,
        }


class AgentProvider(ABC):
    """One worker execution seam. MSB calls ``execute``; the provider owns
    how the goal becomes a result (local DAG, CLI subprocess, Paseo)."""

    spec: ProviderSpec

    def available(self) -> bool:
        """True when this provider can run right now (hermetic for tests)."""
        return True

    def unavailable_reason(self) -> str:
        """Why this provider cannot run right now ("" = it can)."""
        return ""

    def capabilities(self) -> Tuple[str, ...]:
        return self.spec.capabilities

    @abstractmethod
    async def execute(
        self,
        goal: str,
        *,
        context: Optional[Dict[str, Any]] = None,
        session: str = "default",
    ) -> ProviderResult:
        ...


class LocalAgentProvider(AgentProvider):
    """The sovereign local slice as a provider: intent -> plan -> gated
    execute -> verify, run in this process (delegates to agent.handle)."""

    spec = ProviderSpec(
        provider_id="local.slice",
        display_name="Local Sovereign Slice",
        kind="local",
        capabilities=("search_query", "chat", "vault_write"),
        max_risk_tier=3,
        timeout_s=300.0,
    )

    def __init__(self, *, client: Any = None, provider: Any = None, gate: Any = None) -> None:
        self._client = client
        self._provider = provider
        self._gate = gate

    async def execute(
        self,
        goal: str,
        *,
        context: Optional[Dict[str, Any]] = None,
        session: str = "default",
    ) -> ProviderResult:
        from msb_v3.agent.handle import handle

        context = context or {}
        started = time.perf_counter()
        result = await handle(
            goal,
            client=self._client,
            provider=self._provider,
            gate=self._gate,
            session=session,
            tenant=context.get("tenant", "wilson-vault"),
            approve=bool(context.get("approve", False)),
            output_dir=context.get("output_dir"),
        )
        duration = round(time.perf_counter() - started, 4)
        return ProviderResult(
            ok=result.ok,
            output=str(result.trace.get("outcome", {})) if result.trace else "",
            artifacts={"deterministic_hash": result.deterministic_hash, "run_id": result.run_id},
            error=result.error,
            duration_s=duration,
        )


class CliAgentProvider(AgentProvider):
    """An external CLI agent (Claude Code / Codex / OpenCode) as a bounded
    worker: task prompt appended to the command, subprocess in an isolated
    worktree, output captured (bounded), killed on timeout, result retrieved.

    HIGH risk by construction — see the module docstring's safety note.
    """

    def __init__(self, command: Tuple[str, ...], *, provider_id: str = "", display_name: str = "", timeout_s: float = 120.0) -> None:
        binary = command[0] if command else ""
        # provider_id derives from the binary *name* (never the full path —
        # the id doubles as a temp-prefix slug).
        base = Path(binary).name if binary else "agent"
        self.spec = ProviderSpec(
            provider_id=provider_id or f"cli.{base}",
            display_name=display_name or f"CLI agent: {binary}",
            kind="cli",
            command=command,
            capabilities=(),
            max_risk_tier=4,
            timeout_s=timeout_s,
        )

    def available(self) -> bool:
        if not self.spec.command:
            return False
        return shutil.which(self.spec.command[0]) is not None

    def unavailable_reason(self) -> str:
        if self.available():
            return ""
        return f"{self.spec.command[0]} not on PATH"

    async def execute(
        self,
        goal: str,
        *,
        context: Optional[Dict[str, Any]] = None,
        session: str = "default",
    ) -> ProviderResult:
        if not self.available():
            return ProviderResult(ok=False, error=f"provider unavailable: {self.spec.command[0]} not on PATH")
        slug = "".join(c if c.isalnum() or c in "-_." else "_" for c in self.spec.provider_id)
        worktree = Path(tempfile.mkdtemp(prefix=f"{slug}_"))
        cmd = list(self.spec.command) + [goal]
        env = {**os.environ, "MSB_WORKTREE": str(worktree), "MSB_SESSION": session}
        started = time.perf_counter()
        # Observation sink (same contract as the Paseo adapter): each
        # non-empty stdout line is streamed into the unified task as an
        # OBSERVATION_RECORDED sample while the worker runs — the task
        # document becomes a live record of what the CLI worker actually
        # did, not just its final output.
        context = context or {}
        on_observation = context.get("observation_sink")
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(worktree),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except FileNotFoundError:
            return ProviderResult(ok=False, error=f"command not found: {cmd[0]}")
        chunks: List[str] = []
        total_len = 0
        update_count = 0

        async def _drain() -> None:
            nonlocal total_len, update_count
            assert proc.stdout is not None
            while True:
                raw = await proc.stdout.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace")
                if on_observation is not None and line.strip():
                    update_count += 1
                    sample = {
                        "source": "cli.output",
                        "observed_at": datetime.now(timezone.utc).isoformat(),
                        "update_count": update_count,
                        "content": line.rstrip(),
                    }
                    try:
                        await on_observation(sample)
                    except Exception as exc:  # noqa: BLE001 — the sink is best-effort
                        logger.warning("cli observation sink failed for %s: %s", self.spec.provider_id, exc)
                if total_len < _MAX_OUTPUT_BYTES:
                    chunks.append(line)
                    total_len += len(line)

        try:
            await asyncio.wait_for(_drain(), timeout=self.spec.timeout_s)
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except Exception:  # noqa: BLE001 — best-effort cleanup
                logger.warning("failed to kill timed-out provider %s", self.spec.provider_id)
            return ProviderResult(
                ok=False,
                error=f"timed out after {self.spec.timeout_s}s",
                duration_s=round(time.perf_counter() - started, 4),
            )
        await proc.wait()
        duration = round(time.perf_counter() - started, 4)
        text = "".join(chunks)[:_MAX_OUTPUT_BYTES]
        artifacts: Dict[str, Any] = {}
        for p in sorted(worktree.iterdir()):
            if p.is_file() and len(artifacts) < _MAX_ARTIFACTS:
                artifacts[p.name] = {"bytes": p.stat().st_size}
        ok = proc.returncode == 0
        return ProviderResult(
            ok=ok,
            output=text,
            artifacts=artifacts,
            error=None if ok else f"exit code {proc.returncode}",
            duration_s=duration,
        )


def _daemon_reachable(url: str) -> bool:
    """Cheap liveness floor for the Paseo daemon: can we open a TCP
    connection to its host:port? Full protocol reachability is probed by
    /system/health (initialize handshake); this keeps provider discovery
    fast and side-effect free."""
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 6767
    try:
        with socket.create_connection((host, port), timeout=0.4):
            return True
    except OSError:
        return False


class PaseoAgentProvider(AgentProvider):
    """A Paseo-managed external agent (Claude Code / Codex / OpenCode) as a
    bounded worker: the daemon creates an isolated git worktree, MSB sends
    the task, blocks on the daemon, and parks every permission request on an
    operator-gated Vesta approval before it reaches the worker.

    HIGH risk by construction — a Paseo agent runs with the operator's user
    account; worktree isolation bounds where it *should* write but is NOT a
    sandbox. Operator registration with scoped capabilities required.
    """

    def __init__(
        self,
        paseo_provider: str = "claude",
        *,
        adapter: Any = None,
        timeout_s: float = 600.0,
        available: Optional[bool] = None,
    ) -> None:
        if paseo_provider not in ("claude", "codex", "opencode"):
            raise ValueError(f"unknown paseo provider: {paseo_provider}")
        self._paseo_provider = paseo_provider
        self._adapter = adapter
        self._available_override = available
        self.spec = ProviderSpec(
            provider_id=f"paseo.{paseo_provider}",
            display_name=f"Paseo agent: {paseo_provider}",
            kind="paseo",
            capabilities=(),
            max_risk_tier=4,
            timeout_s=timeout_s,
        )

    def available(self) -> bool:
        if self._available_override is not None:
            return self._available_override
        return _daemon_reachable(settings.paseo_url)

    def unavailable_reason(self) -> str:
        if self.available():
            return ""
        return f"paseo daemon unreachable at {settings.paseo_url}"

    async def execute(
        self,
        goal: str,
        *,
        context: Optional[Dict[str, Any]] = None,
        session: str = "default",
    ) -> ProviderResult:
        from msb_v3.agent.paseo import PaseoAdapter, PaseoMcpError

        context = context or {}
        started = time.perf_counter()
        try:
            adapter = self._adapter if self._adapter is not None else PaseoAdapter()
            cwd = context.get("repo") or context.get("cwd") or os.getcwd()
            run = await adapter.drive_run(
                goal=goal,
                cwd=str(cwd),
                base_branch=str(context.get("base_branch") or "main"),
                provider=self._paseo_provider,
                model=context.get("model"),
                task_id=str(context.get("task_id") or ""),
                timeout_s=self.spec.timeout_s,
                on_observation=context.get("observation_sink"),
            )
            duration = round(time.perf_counter() - started, 4)
            if run.get("ok"):
                return ProviderResult(
                    ok=True,
                    output=str(run.get("last_message") or ""),
                    artifacts={"paseo_agent_id": run.get("agent_id"), "status": run.get("status"), **run.get("extra", {})},
                    duration_s=duration,
                )
            return ProviderResult(
                ok=False,
                output=str(run.get("last_message") or ""),
                artifacts={"paseo_agent_id": run.get("agent_id"), "status": run.get("status")},
                error=run.get("error"),
                duration_s=duration,
            )
        except PaseoMcpError as exc:
            return ProviderResult(ok=False, error=f"paseo: {exc}", duration_s=round(time.perf_counter() - started, 4))
        except Exception as exc:  # noqa: BLE001 — provider must fail with evidence, not crash the run
            return ProviderResult(ok=False, error=f"{type(exc).__name__}: {exc}", duration_s=round(time.perf_counter() - started, 4))


class DeepSeekAgentProvider(AgentProvider):
    """DeepSeek's native OpenAI-compatible API as a worker. The frontier seam
    (``fabric.model_router.FrontierClient``) already defaults to DeepSeek; this
    provider makes it first-class: ``execute()`` drives a full governed run
    through ``agent.handle()`` with a DeepSeek-backed client, so MoIE ->
    ActionGate -> evidence spine -> ledger -> receipt all fire with zero new
    governance code (the same pattern as ``LocalAgentProvider``).
    """

    spec = ProviderSpec(
        provider_id="api.deepseek",
        display_name="DeepSeek (native API)",
        kind="api",
        capabilities=("search_query", "chat", "vault_write"),
        max_risk_tier=3,
        timeout_s=300.0,
    )

    def __init__(self, *, client: Any = None) -> None:
        self._client = client

    def available(self) -> bool:
        if self._client is not None:
            return True
        return bool(settings.deepseek_api_key)

    def unavailable_reason(self) -> str:
        if self.available():
            return ""
        return "DEEPSEEK_API_KEY not set"

    async def execute(
        self,
        goal: str,
        *,
        context: Optional[Dict[str, Any]] = None,
        session: str = "default",
    ) -> ProviderResult:
        from msb_v3.agent.handle import handle
        from msb_v3.local_ai.deepseek import DeepSeekClient

        client = self._client if self._client is not None else DeepSeekClient()
        context = context or {}
        started = time.perf_counter()
        result = await handle(
            goal,
            client=client,
            session=session,
            tenant=context.get("tenant", "wilson-vault"),
            approve=bool(context.get("approve", False)),
            output_dir=context.get("output_dir"),
        )
        duration = round(time.perf_counter() - started, 4)
        return ProviderResult(
            ok=result.ok,
            output=str(result.trace.get("outcome", {})) if result.trace else "",
            artifacts={"deterministic_hash": result.deterministic_hash, "run_id": result.run_id},
            error=result.error,
            duration_s=duration,
        )


def default_providers() -> Tuple[AgentProvider, ...]:
    """Local slice + the common CLI workers + Paseo-managed agents +
    the DeepSeek API provider (availability checked lazily)."""
    return (
        LocalAgentProvider(),
        DeepSeekAgentProvider(),
        CliAgentProvider(("claude", "-p")),
        CliAgentProvider(("codex", "exec")),
        CliAgentProvider(("opencode", "run")),
        PaseoAgentProvider("claude"),
        PaseoAgentProvider("codex"),
        PaseoAgentProvider("opencode"),
    )


class ProviderRegistry:
    """Deterministic provider selection: available + capable + within tier."""

    def __init__(self, providers: Optional[Tuple[AgentProvider, ...]] = None) -> None:
        self._providers = providers if providers is not None else default_providers()

    def get(self, provider_id: str) -> Optional[AgentProvider]:
        for p in self._providers:
            if p.spec.provider_id == provider_id:
                return p
        return None

    def list(self) -> List[Dict[str, Any]]:
        return [
            {
                "provider_id": p.spec.provider_id,
                "display_name": p.spec.display_name,
                "kind": p.spec.kind,
                "command": list(p.spec.command),
                "capabilities": list(p.spec.capabilities),
                "max_risk_tier": p.spec.max_risk_tier,
                "available": p.available(),
            }
            for p in self._providers
        ]

    def select(
        self,
        *,
        required_capabilities: Tuple[str, ...] = (),
        max_risk_tier: int = 4,
        available_only: bool = True,
    ) -> List[AgentProvider]:
        """Available providers that carry every required capability and stay
        within the risk tier, in registration order (deterministic)."""
        chosen = []
        for p in self._providers:
            caps = set(p.spec.capabilities)
            if required_capabilities and not all(c in caps for c in required_capabilities):
                continue
            if p.spec.max_risk_tier > max_risk_tier:
                continue
            if available_only and not p.available():
                continue
            chosen.append(p)
        return chosen
