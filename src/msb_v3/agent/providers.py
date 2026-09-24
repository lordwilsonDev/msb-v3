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
    dsh     DshAgentProvider    — DeepSeek Harness (dsh), DeepSeek AI's
                                  plugin-based agent harness, run as a bounded
                                  subprocess (--profile headless) in an
                                  isolated worktree, same governance as cli.

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
import shlex
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


def _default_spine(injected: Any = None) -> Any:
    """A usable evidence spine, or None when unavailable (best-effort: a
    spine outage degrades provenance, never the run)."""
    if injected is not None:
        return injected
    try:
        from msb_v3.evidence.spine import DecisionEvidenceStore

        return DecisionEvidenceStore()
    except Exception as exc:  # noqa: BLE001 — provenance must never break the run
        logger.warning("default evidence spine unavailable: %s", exc)
        return None

# Max output/artifact bytes captured from a CLI worker (bounded evidence).
_MAX_OUTPUT_BYTES = 200_000
_MAX_ARTIFACTS = 20

# Env vars a subprocess worker is allowed to inherit. Passing {**os.environ}
# hands every secret in the process (operator token, provider API keys, cloud
# creds) to a HIGH-risk worker that runs unsandboxed — see the module safety
# note. The allowlist is: enough to run node/the binary and reach local
# Ollama, plus the two MSB_* markers the worker needs. Nothing else.
_CHILD_ENV_PASSTHROUGH = (
    "PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "SHELL", "USER", "LOGNAME",
    "SSL_CERT_FILE", "NODE_EXTRA_CA_CERTS",
    "OLLAMA_API_KEY", "OLLAMA_URL", "OLLAMA_HOST", "OLLAMA_MODEL",
)


def scrub_debug_env(env: Dict[str, str]) -> Dict[str, str]:
    """Drop macOS malloc-debug vars from a child environment.

    When the parent inherits ``MallocStackLogging`` / ``MallocScribble`` /
    ``NSZombieEnabled`` (from a leaked Xcode/Instruments session or a stray
    ``launchctl setenv``), every spawned ``python`` prints
    ``MallocStackLogging: can't turn off malloc stack logging ...`` at
    startup — 12.7k lines/log per JOB-004. msb-v3's own subprocesses must
    not propagate these regardless of where they came from.
    """
    return {
        k: v
        for k, v in env.items()
        if not k.startswith("Malloc") and k != "NSZombieEnabled"
    }


def _child_env(worktree: str, session: str) -> Dict[str, str]:
    """Scoped environment for a subprocess worker: allowlisted passthrough
    vars that are actually set, plus the worktree/session markers. Never
    leaks the parent's secrets (and never propagates malloc-debug noise)."""
    env: Dict[str, str] = {}
    for key in _CHILD_ENV_PASSTHROUGH:
        value = os.environ.get(key)
        if value is not None:
            env[key] = value
    env["MSB_WORKTREE"] = worktree
    env["MSB_SESSION"] = session
    return scrub_debug_env(env)


@dataclass(frozen=True)
class ProviderSpec:
    provider_id: str  # "local.slice" | "cli.claude" | "cli.codex" | "cli.opencode"
    display_name: str
    kind: str  # "local" | "cli" | "dsh" | "api" | "paseo"
    command: Tuple[str, ...] = ()  # cli only: the executable + fixed args
    capabilities: Tuple[str, ...] = ()
    max_risk_tier: int = 2
    timeout_s: float = 120.0
    contract_version: str = "1"  # ProviderContract version this spec conforms to


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
    how the goal becomes a result (local DAG, CLI subprocess, Paseo).

    ProviderContract v1 conformance: every production provider must
    satisfy the contract defined in ``ProviderContract``. The conformance
    suite (tests/contracts/test_provider_contract.py) verifies this.
    """

    spec: ProviderSpec

    def available(self) -> bool:
        """True when this provider can run right now (hermetic for tests)."""
        return True

    def unavailable_reason(self) -> str:
        """Why this provider cannot run right now ("" = it can)."""
        return ""

    def capabilities(self) -> Tuple[str, ...]:
        return self.spec.capabilities

    def health(self) -> Dict[str, Any]:
        """Health check — returns a dict with at minimum ``ok: bool``.

        Default implementation delegates to ``available()``. Providers
        that can perform deeper health checks (e.g. TCP reachability,
        API key validation) should override this.
        """
        return {
            "ok": self.available(),
            "reason": self.unavailable_reason(),
            "provider_id": self.spec.provider_id,
        }

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

    def __init__(self, *, client: Any = None, provider: Any = None, gate: Any = None, spine: Any = None) -> None:
        self._client = client
        self._provider = provider
        self._gate = gate
        self._spine = spine

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
            spine=_default_spine(self._spine),
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
            # Deliberately empty, and load-bearing: a declared capability is a
            # TRUST grant, not a description. An external agent on the
            # operator's account gets none until the operator registers scoped
            # ones — "no implicit trust" (tests/integrations/
            # test_cli_provider_isolation.py states the model and pins it, incl.
            # TestCliProviderCapabilityEscape). Routing therefore uses `kind`,
            # which those tests name as the registry's routing key for CLI.
            # Adding a name here without an operator grant is a privilege
            # escalation, not a selection hint.
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
        env = _child_env(str(worktree), session)
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
            shutil.rmtree(worktree, ignore_errors=True)
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
            shutil.rmtree(worktree, ignore_errors=True)
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
        shutil.rmtree(worktree, ignore_errors=True)
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


class AnthropicAgentProvider(AgentProvider):
    """Anthropic's native Messages API as a worker — the third harness
    (api.anthropic), same governed pattern as the retired DeepSeek client
    (D1, 2026-09-09): ``execute()`` drives a
    full run through ``agent.handle()`` with an Anthropic-backed client, so
    MoIE -> ActionGate -> evidence spine -> ledger -> receipt all fire with
    zero new governance code.
    """

    spec = ProviderSpec(
        provider_id="api.anthropic",
        display_name="Anthropic (native API)",
        kind="api",
        capabilities=("search_query", "chat", "vault_write"),
        max_risk_tier=3,
        timeout_s=300.0,
    )

    def __init__(self, *, client: Any = None, spine: Any = None) -> None:
        self._client = client
        self._spine = spine

    def available(self) -> bool:
        if self._client is not None:
            return True
        return bool(settings.anthropic_api_key)

    def unavailable_reason(self) -> str:
        if self.available():
            return ""
        return "ANTHROPIC_API_KEY not set"

    async def execute(
        self,
        goal: str,
        *,
        context: Optional[Dict[str, Any]] = None,
        session: str = "default",
    ) -> ProviderResult:
        from msb_v3.agent.handle import handle
        from msb_v3.local_ai.anthropic import AnthropicClient

        client = self._client if self._client is not None else AnthropicClient()
        context = context or {}
        started = time.perf_counter()
        result = await handle(
            goal,
            client=client,
            spine=_default_spine(self._spine),
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


class DshAgentProvider(AgentProvider):
    """DeepSeek Harness (dsh) as a governed subprocess worker.

    DeepSeek Harness is DeepSeek AI's open-source plugin-based agent harness
    (MIT-licensed, ``@deepseek-ai/dsh``). This provider runs it as a bounded
    subprocess in an isolated worktree — the same governance pattern as
    ``CliAgentProvider``:

    - ``dsh --profile headless <goal>`` runs the task headlessly (no web UI),
      persisting the session, then exits with the last assistant message on
      stdout.
    - The worktree is isolated (temp dir, ``MSB_WORKTREE`` env var).
    - Stdout/stderr are captured (bounded).
    - Timeout kills the process (fail-closed).
    - Every execution produces an evidence receipt through the existing
      ``CliAgentProvider`` pattern (goal → subprocess → output → receipt).

    HIGH risk (tier 4) by construction — the subprocess runs with the
    operator's user account, same as ``CliAgentProvider``. The worktree
    bounds where it *should* write but is not a sandbox.

    Availability: ``dsh`` or ``npx @deepseek-ai/dsh`` on PATH (resolved from
    ``DSH_BINARY``, default ``"dsh"``). The provider is honest: if the binary
    is not resolvable, ``available()`` returns ``False`` and
    ``unavailable_reason()`` names the missing binary.
    """

    spec = ProviderSpec(
        provider_id="dsh.headless",
        display_name="DeepSeek Harness (dsh headless)",
        kind="dsh",
        capabilities=(),
        max_risk_tier=4,
        timeout_s=600.0,
    )

    def __init__(
        self,
        *,
        command: Optional[Tuple[str, ...]] = None,
        timeout_s: Optional[float] = None,
    ) -> None:
        """``command`` override (for tests — inject a fake script).
        ``timeout_s`` override (default from ``settings.dsh_timeout_s``)."""
        self._command = command
        if timeout_s is not None:
            self.spec = ProviderSpec(
                provider_id=self.spec.provider_id,
                display_name=self.spec.display_name,
                kind=self.spec.kind,
                capabilities=self.spec.capabilities,
                max_risk_tier=self.spec.max_risk_tier,
                timeout_s=timeout_s,
            )

    def _resolve_command(self) -> Tuple[str, ...]:
        """Resolve the dsh command from settings or the injected override."""
        if self._command is not None:
            return self._command
        tokens = shlex.split(settings.dsh_binary) if settings.dsh_binary else []
        return tuple(tokens) if tokens else ("dsh",)

    def available(self) -> bool:
        tokens = self._resolve_command()
        if not tokens:
            return False
        return shutil.which(tokens[0]) is not None

    def unavailable_reason(self) -> str:
        if self.available():
            return ""
        tokens = self._resolve_command()
        binary = tokens[0] if tokens else "dsh"
        return f"{binary} not on PATH (set DSH_BINARY to an executable or npx prefix)"

    async def execute(
        self,
        goal: str,
        *,
        context: Optional[Dict[str, Any]] = None,
        session: str = "default",
    ) -> ProviderResult:
        if not self.available():
            return ProviderResult(
                ok=False,
                error=f"provider unavailable: {self.unavailable_reason()}",
            )
        slug = "".join(c if c.isalnum() or c in "-_." else "_" for c in self.spec.provider_id)
        worktree = Path(tempfile.mkdtemp(prefix=f"{slug}_"))
        cmd = list(self._resolve_command())
        cmd.extend(["--profile", settings.dsh_profile, goal])
        env = _child_env(str(worktree), session)
        started = time.perf_counter()
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
            shutil.rmtree(worktree, ignore_errors=True)
            return ProviderResult(
                ok=False,
                error=f"command not found: {cmd[0]}",
            )
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
                        "source": "dsh.output",
                        "observed_at": datetime.now(timezone.utc).isoformat(),
                        "update_count": update_count,
                        "content": line.rstrip(),
                    }
                    try:
                        await on_observation(sample)
                    except Exception as exc:  # noqa: BLE001 — the sink is best-effort
                        logger.warning(
                            "dsh observation sink failed for %s: %s",
                            self.spec.provider_id,
                            exc,
                        )
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
                logger.warning(
                    "failed to kill timed-out provider %s",
                    self.spec.provider_id,
                )
            shutil.rmtree(worktree, ignore_errors=True)
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
        shutil.rmtree(worktree, ignore_errors=True)
        return ProviderResult(
            ok=ok,
            output=text,
            artifacts=artifacts,
            error=None if ok else f"exit code {proc.returncode}",
            duration_s=duration,
        )


def cli_provider_timeout_s() -> float:
    """Time budget declared once for every CLI agent worker.

    It used to be two values for the same worker: this class defaulted to 120 s,
    while `factory/builders.py` constructed its own with 300 s — so a worker's
    budget depended on which code built it, and the Spec's ``timeout_s`` (the
    field the Registry is supposed to select on) said 120 while the factory ran
    at 300. 300 s is the value the factory already used for real code-writing
    runs, and it sits below the 600 s the dsh/paseo workers declare. Operators
    move it with ``MSB_CLI_TIMEOUT_S``.
    """
    try:
        return float(os.environ.get("MSB_CLI_TIMEOUT_S", "300"))
    except ValueError:
        # A non-numeric override must not silently become a 0 s budget.
        return 300.0


def default_providers() -> Tuple[AgentProvider, ...]:
    """Local slice + the common CLI workers + DeepSeek Harness (dsh) +
    Paseo-managed agents + the Anthropic API provider (availability checked
    lazily). The DeepSeek API provider was retired with the frontier seam
    (D1, 2026-09-09)."""
    _cli_timeout = cli_provider_timeout_s()
    return (
        LocalAgentProvider(),
        AnthropicAgentProvider(),
        DshAgentProvider(),
        CliAgentProvider(("claude", "-p"), timeout_s=_cli_timeout),
        CliAgentProvider(("codex", "exec"), timeout_s=_cli_timeout),
        CliAgentProvider(("opencode", "run"), timeout_s=_cli_timeout),
        PaseoAgentProvider("claude"),
        PaseoAgentProvider("codex"),
        PaseoAgentProvider("opencode"),
    )


# ---------------------------------------------------------------------------
# Drop-in registration
# ---------------------------------------------------------------------------
#
# A worker arrives by *registration*, not by editing this file. The operator
# names a `module:attr` entry point in MSB_PROVIDER_PLUGINS; the loader below
# validates it against the seam and the Registry routes to it. Nothing in
# `default_providers()` needs to change, and no consumer learns a new name —
# which is the whole point of a seam for systems that show up later.

# Refusals are logged once per (source, reason): a Registry is constructed on
# every `handle()` call, so logging on every construction would bury the line
# that matters.
_LOGGED_REFUSALS: set[tuple[str, str]] = set()


@dataclass(frozen=True)
class ProviderLoadFailure:
    """A configured worker that was refused, and why.

    Recorded rather than swallowed. A worker that fails to load silently is a
    substitution hazard: the Registry routes to whatever else is available and
    nothing says the configured one is missing.
    """

    source: str
    reason: str


def provider_plugin_specs() -> Tuple[str, ...]:
    """`module:attr` worker entry points named by MSB_PROVIDER_PLUGINS."""
    raw = getattr(settings, "provider_plugins", "") or ""
    return tuple(spec.strip() for spec in raw.split(",") if spec.strip())


def _known_capability_vocabulary() -> frozenset[str]:
    """Names a worker may declare — the two tables that own the vocabulary.

    Imported here, not at module scope: `tools.registry` and `agent.safety` sit
    above this module, and a worker catalogue must not become an import cycle.
    """
    from msb_v3.agent.safety import TOOL_CAPABILITY
    from msb_v3.tools.registry import TOOLS

    return frozenset(TOOL_CAPABILITY) | frozenset(
        cap for tool in TOOLS.values() for cap in tool.required_capabilities
    )


def _refusal(provider: Any, *, taken: frozenset[str]) -> str:
    """Why `provider` may not be registered — "" when it may.

    Fail-closed: anything not clearly a conforming worker is refused, and the
    reason names the missing piece so a registration can be fixed without
    reading this module.
    """
    spec = getattr(provider, "spec", None)
    if spec is None:
        return "no `spec` attribute — a worker must expose a ProviderSpec"
    for attr in ("provider_id", "kind", "capabilities", "max_risk_tier", "timeout_s"):
        if not hasattr(spec, attr):
            return f"ProviderSpec has no `{attr}`"
    if not isinstance(spec.provider_id, str) or not spec.provider_id.strip():
        return "provider_id must be a non-empty string"
    if spec.provider_id in taken:
        return f"provider_id {spec.provider_id!r} is already registered by another plugin"
    if not isinstance(spec.kind, str) or not spec.kind.strip():
        return "kind must be a non-empty string"
    if not isinstance(spec.max_risk_tier, int) or not 1 <= spec.max_risk_tier <= 4:
        return f"max_risk_tier {spec.max_risk_tier!r} is outside 1..4"
    if not isinstance(spec.timeout_s, (int, float)) or spec.timeout_s <= 0:
        return f"timeout_s {spec.timeout_s!r} must be positive"
    if not isinstance(spec.command, tuple):
        return "command must be a tuple"
    if not isinstance(spec.capabilities, tuple):
        return "capabilities must be a tuple"
    # A declared capability is a trust grant (see CliAgentProvider's spec note).
    # Accepting an unknown name would look registered while resolving to nothing:
    # governance.capability_registry returns None for ids it does not know.
    unknown = sorted(set(spec.capabilities) - _known_capability_vocabulary())
    if unknown:
        return (
            f"declares {unknown}, which no capability table knows — a name must be "
            f"a governed tool (safety.TOOL_CAPABILITY) or a tool capability "
            f"(tools.registry), or it can be selected on but never gated"
        )
    for attr in ("available", "unavailable_reason", "execute"):
        if not callable(getattr(provider, attr, None)):
            return f"`{attr}()` is missing or not callable"
    return ""


def _resolve_plugin(source: str) -> Any:
    """Import `module:attr`, calling it when it is a class or factory."""
    import importlib

    module_name, _, attr = source.partition(":")
    if not module_name or not attr:
        raise ValueError("expected the form 'module:attr'")
    module = importlib.import_module(module_name)
    target = getattr(module, attr)
    if isinstance(target, AgentProvider):
        return target
    if callable(target):
        return target()
    raise TypeError(f"{source} is neither an AgentProvider nor a factory for one")


def load_provider_plugins(
    specs: Optional[Tuple[str, ...]] = None,
    *,
    existing: Tuple[AgentProvider, ...] = (),
) -> Tuple[Tuple[AgentProvider, ...], Tuple[ProviderLoadFailure, ...]]:
    """Load the configured workers. Returns ``(loaded, refusals)``.

    A plugin whose ``provider_id`` matches a built-in *replaces* it — the seam
    promises components can be replaced, not only added — which the Registry
    applies; the loader only refuses two *plugins* claiming one id, since that is
    a configuration error rather than an override and resolving it by import
    order would be invisible.
    """
    requested = provider_plugin_specs() if specs is None else tuple(specs)
    loaded: List[AgentProvider] = []
    failures: List[ProviderLoadFailure] = []
    builtin_kinds = {p.spec.kind for p in existing}
    for source in requested:
        failure: Optional[ProviderLoadFailure] = None
        try:
            provider = _resolve_plugin(source)
        except Exception as exc:  # noqa: BLE001 — any import/build failure is a refusal
            failure = ProviderLoadFailure(source, f"{type(exc).__name__}: {exc}")
        else:
            reason = _refusal(provider, taken=frozenset(p.spec.provider_id for p in loaded))
            if reason:
                failure = ProviderLoadFailure(source, reason)
            else:
                if provider.spec.kind not in builtin_kinds:
                    logger.info(
                        "provider plugin %s declares new kind %r — no consumer routes "
                        "on it yet (routing keys are declared by consumers, e.g. "
                        "factory.builders._WORKER_KIND)",
                        source,
                        provider.spec.kind,
                    )
                loaded.append(provider)
        if failure is not None:
            failures.append(failure)
            key = (failure.source, failure.reason)
            if key not in _LOGGED_REFUSALS:
                _LOGGED_REFUSALS.add(key)
                logger.error("provider plugin refused: %s — %s", failure.source, failure.reason)
    return tuple(loaded), tuple(failures)


class ProviderRegistry:
    """Deterministic provider selection: available + capable + within tier."""

    def __init__(self, providers: Optional[Tuple[AgentProvider, ...]] = None) -> None:
        self._load_failures: Tuple[ProviderLoadFailure, ...] = ()
        if providers is None:
            builtins = default_providers()
            loaded, self._load_failures = load_provider_plugins(existing=builtins)
            replacements = {p.spec.provider_id: p for p in loaded}
            replaced = set(replacements) & {p.spec.provider_id for p in builtins}
            if replaced:
                logger.info(
                    "provider plugin(s) replace built-in(s): %s", sorted(replaced)
                )
            # Substituted IN PLACE, not appended. Selection is registration order,
            # so a plugin that only removed the built-in's id would leave the
            # original's slot empty and an *earlier* worker would win — the
            # override would silently do nothing (found by
            # test_a_dropped_in_worker_takes_over_the_factory_with_no_consumer_edit).
            # Genuinely new workers keep their configured order, after the built-ins.
            composed: List[AgentProvider] = []
            for builtin in builtins:
                substitute = replacements.pop(builtin.spec.provider_id, None)
                composed.append(substitute if substitute is not None else builtin)
            composed.extend(replacements.values())
            self._providers = tuple(composed)
        else:
            self._providers = providers

    def load_failures(self) -> Tuple[ProviderLoadFailure, ...]:
        """Configured workers that were refused, with their reasons."""
        return self._load_failures

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
