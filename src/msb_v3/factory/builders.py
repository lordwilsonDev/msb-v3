"""Software Factory builders (sovereign-architecture §4.2.6 — implement stage).

A Builder turns the plan into a real change inside an **isolated worktree**
(a copy of the repo — the original is never touched). Two builders:

    PatchBuilder    deterministic — runs a script/patch in the worktree
    CliAgentBuilder delegates to an agent worker chosen by the ProviderRegistry
                    with the plan as the goal; fails loudly without a funded
                    model, never silently

The diff/changed-files evidence is computed against the original repo by
the factory (see pipeline.build), not claimed by the builder.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Tuple

from msb_v3.factory.models import BuildResult, Plan

# Never copied into the worktree: secrets, version control, deps, data.
_EXCLUDE_DIRS = {
    ".git", ".venv", "venv", "env", "node_modules", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "data", ".env",
    ".terraform", ".next", "dist", "build", "logs",
}


def create_worktree(repo_path: str) -> str:
    """Copy the repo into an isolated temp worktree (git-free, secret-free).

    Returns the worktree path. The caller owns cleanup.
    """
    src = Path(repo_path).resolve()
    worktree = Path(tempfile.mkdtemp(prefix="sf_worktree_"))

    def _ignore(directory: str, names: list[str]) -> list[str]:
        return [n for n in names if n in _EXCLUDE_DIRS or n.endswith((".pyc", ".pyo"))]

    shutil.copytree(src, worktree, ignore=_ignore, dirs_exist_ok=True, symlinks=True)
    return str(worktree)


def compute_changes(repo_path: str, worktree: str, *, max_diff_bytes: int = 8000) -> Tuple[list[str], str]:
    """Changed files + bounded unified diff of the worktree vs the repo."""
    import difflib

    src_root = Path(repo_path).resolve()
    wt_root = Path(worktree).resolve()
    changed: list[str] = []
    diff_parts: list[str] = []
    total = 0

    for wt_path in sorted(wt_root.rglob("*")):
        if not wt_path.is_file():
            continue
        rel = wt_path.relative_to(wt_root)
        src_path = src_root / rel
        if not src_path.exists():
            changed.append(str(rel))
            continue
        try:
            if wt_path.read_bytes() != src_path.read_bytes():
                changed.append(str(rel))
        except OSError:
            continue

    for rel_str in changed[:25]:
        # A changed file may not exist on one side: a NEW file has no old
        # content, a DELETED file has no new content. Read what exists and
        # treat the missing side as empty — never skip the diff (an empty
        # diff silently starves the reviewer: the live dogfood's new-file
        # doc got no diff at all, so the reviewer could not see the change).
        try:
            old = (src_root / rel_str).read_text(errors="replace").splitlines(keepends=True)
        except OSError:
            old = []
        try:
            new = (wt_root / rel_str).read_text(errors="replace").splitlines(keepends=True)
        except OSError:
            new = []
        for line in difflib.unified_diff(old, new, fromfile=f"a/{rel_str}", tofile=f"b/{rel_str}", lineterm=""):
            total += len(line) + 1
            if total <= max_diff_bytes:
                diff_parts.append(line)
    return changed, "".join(diff_parts)


class Builder(ABC):
    """One implement actor. Returns BuildResult with its own claim; the
    diff/changed-files evidence is independently computed by the factory."""

    builder_id: str = "builder"
    model: str = ""  # worker identity for the builder != reviewer invariant

    @abstractmethod
    async def build(self, plan: Plan, worktree: str, *, repo_hint: str = "") -> BuildResult:
        ...


class PatchBuilder(Builder):
    """Deterministic builder: runs a script in the worktree.

    The script receives ``MSB_WORKTREE`` (the isolated copy) and must make
    the change itself. Used by tests and deterministic demos.
    """

    builder_id = "patch"
    model = "patch"  # deterministic — not an LLM

    def __init__(self, script: str, *, timeout_s: float = 60.0) -> None:
        self._script = script
        self._timeout_s = timeout_s

    async def build(self, plan: Plan, worktree: str, *, repo_hint: str = "") -> BuildResult:
        from msb_v3.agent.providers import scrub_debug_env
        env = scrub_debug_env({**os.environ, "MSB_WORKTREE": worktree, "MSB_GOAL": plan.goal})
        try:
            proc = await asyncio.create_subprocess_exec(
                "/bin/bash", self._script,
                cwd=worktree, env=env,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
            out_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=self._timeout_s)
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except Exception:  # noqa: BLE001 — best-effort cleanup
                pass
            return BuildResult(ok=False, worktree=worktree, error=f"builder timed out after {self._timeout_s}s", builder=self.builder_id)
        text = (out_bytes or b"").decode("utf-8", errors="replace")
        return BuildResult(
            ok=proc.returncode == 0,
            worktree=worktree,
            output_head=text[:2000],
            error=None if proc.returncode == 0 else f"builder exit {proc.returncode}",
            builder=self.builder_id,
        )


# How the factory routes to a worker: by provider KIND, not by capability.
#
# Selecting on a capability would mean asking a CLI provider to declare one, and
# a declared capability is a trust grant — the CLI isolation model requires the
# tuple stay empty until an operator registers scoped capabilities by hand
# ("no implicit trust": tests/integrations/test_cli_provider_isolation.py,
# which names `kind` as the routing key for exactly this reason). An external
# agent on the operator's account must not acquire a granted capability as a
# side effect of the factory wanting to describe it.
_WORKER_KIND = "cli"


def _select_worker(registry: Any = None) -> Any:
    """The default worker for CliAgentBuilder, chosen by the ProviderRegistry.

    A Consumer must not construct a Provider — that is the seam's one invariant
    (`interchangeable-components`: consumers import the Definition, not the
    Provider). This used to be a direct ``CliAgentProvider(("claude", "-p"),
    timeout_s=…)`` right here, which pinned the factory to one binary on PATH,
    ignored the risk tier, and made the swap test a claim about a seam this file
    did not actually use: the Registry could flip providers while the factory
    kept its own.

    Selection inputs are the provider's declared facts — kind, risk tier,
    availability — in registration order, so the choice is deterministic. The
    order is the registry's, not this file's: reorder the registry and the
    factory follows.
    """
    from msb_v3.agent.providers import ProviderRegistry

    reg = registry if registry is not None else ProviderRegistry()
    available = [p for p in reg.select() if p.spec.kind == _WORKER_KIND]
    if available:
        return available[0]
    # Nothing is available. Keep the honest-failure path rather than raising:
    # `build()` below reports `unavailable_reason()` as a recorded BuildResult
    # error, and the four in-tree callers construct this builder bare, so a raise
    # here would replace a recorded failure with a 500. Selecting the first
    # registered worker with availability ignored preserves that, and it can
    # still say *why* it cannot run.
    registered = [
        p
        for p in reg.select(available_only=False)
        if p.spec.kind == _WORKER_KIND
    ]
    if registered:
        return registered[0]
    raise LookupError(
        f"no kind='{_WORKER_KIND}' provider is registered — CliAgentBuilder has no "
        f"worker to delegate to"
    )


class CliAgentBuilder(Builder):
    """Delegates implementation to an agent worker (CLI subprocess).

    The plan is the worker's goal; the worker runs inside the worktree.
    Without a funded provider the worker fails loudly and the factory
    records the honest error — implementation is never faked.

    The worker comes from the ProviderRegistry unless one is injected. Its time
    budget is the worker's own declared ``spec.timeout_s``
    (`cli_provider_timeout_s()`, MSB_CLI_TIMEOUT_S) rather than a second number
    chosen here.
    """

    builder_id = "cli-agent"

    def __init__(self, provider: Any = None, *, registry: Any = None) -> None:
        self._provider = provider if provider is not None else _select_worker(registry)
        # Builder model = the worker's identity ("cli.claude" -> "claude"),
        # used by the reviewer-panel invariant so a worker never reviews itself.
        self.model = self._provider.spec.provider_id.split(".")[-1]

    async def build(self, plan: Plan, worktree: str, *, repo_hint: str = "") -> BuildResult:
        if not self._provider.available():
            return BuildResult(
                ok=False, worktree=worktree,
                error=f"builder provider unavailable: {self._provider.unavailable_reason()}",
                builder=self.builder_id,
            )
        goal = (
            f"Implement this plan in the current repository.\n\n{plan.goal}\n\n"
            "Steps:\n" + "\n".join(f"- {s.title}: {s.action}" for s in plan.steps) +
            "\n\nDo not modify files outside the repo. Run the tests when done."
        )
        try:
            result = await self._provider.execute(
                goal, context={"repo": repo_hint or worktree, "cwd": worktree}, session="factory"
            )
        except Exception as exc:  # noqa: BLE001 — fail with evidence
            return BuildResult(ok=False, worktree=worktree, error=f"builder raised: {type(exc).__name__}: {exc}", builder=self.builder_id)
        return BuildResult(
            ok=result.ok,
            worktree=worktree,
            output_head=result.output[:2000],
            error=result.error,
            builder=self.builder_id,
        )
