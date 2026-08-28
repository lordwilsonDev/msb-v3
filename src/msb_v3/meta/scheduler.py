"""Pure task-graph scheduling over META-0 ``MetaTask`` / ``TaskState``.

No I/O, no orchestration — the scheduler answers "what can run now?", "in what
order?", and "why is this one blocked?" for a set of ``MetaTask`` whose
``dependencies`` name other ``task_id`` values (blueprint §6).

Built by qwen3:8b from a hand-translated spec (one function per task); the
functional bodies are the model's, integration (imports, annotations) is the
checker's. All behaviour pinned by ``tests/meta/test_scheduler.py``.
"""

from __future__ import annotations

from msb_v3.meta.contracts import MetaTask, TaskState


def has_cycle(tasks: list[MetaTask]) -> bool:
    """True iff the dependency graph has a cycle. Unknown dep ids are ignored;
    a self-dependency is a cycle; empty input is False. Never raises."""
    task_ids = {task.task_id for task in tasks}
    graph = {
        task.task_id: [dep for dep in task.dependencies if dep in task_ids]
        for task in tasks
    }
    visited: set[str] = set()
    recursion_stack: set[str] = set()

    def dfs(node: str) -> bool:
        if node in recursion_stack:
            return True
        if node in visited:
            return False
        visited.add(node)
        recursion_stack.add(node)
        for neighbor in graph.get(node, []):
            if dfs(neighbor):
                return True
        recursion_stack.remove(node)
        return False

    return any(dfs(node) for node in task_ids)


def topological_order(tasks: list[MetaTask]) -> list[str]:
    """Every ``task_id`` in an order where each task follows its known
    dependencies. Tie-break: input order. Raises ``ValueError`` on a cycle."""
    if has_cycle(tasks):
        raise ValueError("dependency cycle")
    all_task_ids = {task.task_id for task in tasks}
    adj: dict[str, list[str]] = {}
    in_degree: dict[str, int] = {}
    for task in tasks:
        for dep in task.dependencies:
            if dep in all_task_ids:
                adj.setdefault(dep, []).append(task.task_id)
                in_degree[task.task_id] = in_degree.get(task.task_id, 0) + 1
    queue = [task.task_id for task in tasks if in_degree.get(task.task_id, 0) == 0]
    result: list[str] = []
    while queue:
        current = queue.pop(0)
        result.append(current)
        for neighbor in adj.get(current, []):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)
    return result


def ready_tasks(tasks: list[MetaTask]) -> list[MetaTask]:
    """The tasks eligible to run now: ``BLOCKED`` and every known dependency
    ``PASSED``. An unknown dependency id keeps a task not-ready. Input order."""
    task_map = {task.task_id: task for task in tasks}
    result: list[MetaTask] = []
    for task in tasks:
        if task.state != TaskState.BLOCKED:
            continue
        all_deps_met = True
        for dep in task.dependencies:
            if dep not in task_map or task_map[dep].state != TaskState.PASSED:
                all_deps_met = False
                break
        if all_deps_met:
            result.append(task)
    return result


def blocked_reason(task: MetaTask, by_id: dict[str, MetaTask]) -> str:
    """One-line reason ``task`` is not ready: wrong state, unmet deps, or
    ``"ready"``."""
    if task.state != TaskState.BLOCKED:
        return f"state is {task.state.value}, not BLOCKED"
    unmet = [
        dep_id
        for dep_id in task.dependencies
        if by_id.get(dep_id) is None or by_id[dep_id].state != TaskState.PASSED
    ]
    return "waiting on: " + ", ".join(unmet) if unmet else "ready"
