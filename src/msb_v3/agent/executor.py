"""Agent executor — walks a task DAG to completion (blueprint Layer 3).

Execution semantics (T1.3):
- Tasks run in deterministic topological order (graph.order()).
- Each task's tool calls go through an injected ToolProvider — a narrow
  async interface, so tests use fakes and the real slice wiring (T1.7)
  maps capability names to the MCP bridge / retrieval router.
- Every task gets asyncio.wait_for(task.timeout_s) around its tool calls.
- retry_policy "retry:N" retries transient tool failures up to N times.
- A task's output is a dict of {tool_name: result}; downstream tasks receive
  their declared inputs (parent outputs) keyed by parent task_id.
- Any task that still fails after retries stops the graph; remaining tasks
  are recorded as skipped. No task runs without its parent's success.
- Verification is the grounded registry (T1.4, `verify_task`) by default:
  search_returned_hits / synthesis_nonempty / file_written / none. A custom
  verifier can be injected; with verify=None the registry is used.

Parallel ready-node dispatch is deliberately future work: the slice's graphs
are chains, and sequential topological execution is deterministic and simple.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple

from msb_v3.agent.dag import Task, TaskGraph
from msb_v3.agent.verify import verify_task
from msb_v3.observability.metrics import Metrics


class ToolProvider(Protocol):
    """Narrow async tool surface the executor calls. The real provider maps
    tool names to the MCP bridge / retrieval router / chat harness."""

    async def run_tool(self, name: str, *, task: Task, inputs: Dict[str, Any], session: str) -> Any:
        ...


# verify(task, output) -> {"ok": bool, "detail": str}
Verifier = Callable[[Task, Dict[str, Any]], Dict[str, Any]]


@dataclass
class TaskResult:
    task_id: str
    ok: bool
    output: Dict[str, Any] = field(default_factory=dict)
    verification: Dict[str, Any] = field(default_factory=dict)
    latency_s: float = 0.0
    attempts: int = 0
    error: Optional[str] = None


@dataclass
class ExecReport:
    ok: bool
    goal: str
    results: Tuple[TaskResult, ...] = ()
    skipped: Tuple[str, ...] = ()
    total_latency_s: float = 0.0
    error: Optional[str] = None

    def result_of(self, task_id: str) -> Optional[TaskResult]:
        for r in self.results:
            if r.task_id == task_id:
                return r
        return None


def _retry_attempts(retry_policy: str) -> int:
    """'retry:2' -> 2 retries (3 attempts total). Anything else -> 0 retries."""
    if isinstance(retry_policy, str) and retry_policy.startswith("retry:"):
        try:
            return max(0, int(retry_policy.split(":", 1)[1]))
        except ValueError:
            return 0
    return 0


async def execute_graph(
    graph: TaskGraph,
    provider: ToolProvider,
    *,
    verify: Verifier | None = None,
    session: str = "default",
) -> ExecReport:
    """Execute a TaskGraph through the provider. Returns an ExecReport."""
    started = time.perf_counter()
    results: List[TaskResult] = []
    outputs: Dict[str, Any] = {}

    try:
        ordered = graph.order()
    except ValueError as exc:
        return ExecReport(ok=False, goal=graph.goal, error=f"graph not executable: {exc}")

    for task in ordered:
        inputs = {pid: outputs[pid] for pid in (task.inputs and [i.get("from") for i in task.inputs] or []) if pid in outputs}
        task_started = time.perf_counter()
        attempts = 0
        output: Dict[str, Any] = {}
        error: Optional[str] = None

        retries = _retry_attempts(task.retry_policy)
        for attempt in range(retries + 1):
            attempts = attempt + 1
            try:
                for tool_name in task.tools:
                    result = await asyncio.wait_for(
                        provider.run_tool(tool_name, task=task, inputs=inputs, session=session),
                        timeout=task.timeout_s,
                    )
                    output[tool_name] = result
                error = None
                break
            except asyncio.TimeoutError:
                error = f"timed out after {task.timeout_s}s"
            except Exception as exc:  # noqa: BLE001 — degrade per task, don't crash the loop
                error = f"{type(exc).__name__}: {exc}"

        latency = round(time.perf_counter() - task_started, 4)
        if error is None:
            verification = verify(task, output) if verify is not None else verify_task(task, output)
            task_ok = bool(verification.get("ok", False))
            task_result = TaskResult(
                task_id=task.task_id,
                ok=task_ok,
                output=output,
                verification=verification,
                latency_s=latency,
                attempts=attempts,
            )
        else:
            task_ok = False
            task_result = TaskResult(
                task_id=task.task_id,
                ok=False,
                output=output,
                verification={"ok": False, "detail": error},
                latency_s=latency,
                attempts=attempts,
                error=error,
            )

        results.append(task_result)
        if not task_ok:
            skipped = tuple(t.task_id for t in ordered[ordered.index(task) + 1 :])
            total_latency = round(time.perf_counter() - started, 4)
            Metrics.inc("agentic", "exec:failed")
            Metrics.latency("agentic", total_latency)
            return ExecReport(
                ok=False,
                goal=graph.goal,
                results=tuple(results),
                skipped=skipped,
                total_latency_s=total_latency,
                error=f"task {task.task_id} failed: {task_result.error or task_result.verification.get('detail')}",
            )

        outputs[task.task_id] = output

    total_latency = round(time.perf_counter() - started, 4)
    Metrics.inc("agentic", "exec:completed")
    Metrics.latency("agentic", total_latency)
    return ExecReport(ok=True, goal=graph.goal, results=tuple(results), total_latency_s=total_latency)
