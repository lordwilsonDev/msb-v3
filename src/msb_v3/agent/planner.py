"""Agent planner — turns an Intent into an executable task DAG (blueprint Layer 3).

LLM-first with a deterministic template fallback, mirroring the intent
interpreter: the model proposes tasks (id, goal, parent, capabilities, tools,
verification method, timeout, retry policy), the parser validates each one,
and any failure degrades to the template DAG rather than an error. The loop
can therefore always proceed to execution.

Slice vocabulary (capability → real tool, wired in the executor):
    read_vault      → search_query + vault_read (MCP bridge / retrieval router)
    llm_synthesis   → model generation (ChatHarness)
    write_file      → vault_write (MCP bridge), sandboxed by the safety gate

Determinism: the template DAG is fully deterministic (pinned by tests); the
LLM path is deterministic given the model's output. The trace (T1.6) records
which source produced the graph.
"""

from __future__ import annotations

from typing import Any, Dict, List

from msb_v3.agent.dag import Task, TaskGraph
from msb_v3.agent.intent import Intent, _extract_json, _clean_str_list
from msb_v3.local_ai.llama_client import LlamaCPPClient
from msb_v3.local_ai.ollama import LocalAIClient
from msb_v3.observability.metrics import Metrics

_PLAN_SYSTEM = (
    "You are an agentic task planner. Decompose the user's goal into a small "
    "task DAG and return ONLY a JSON object with a single key \"tasks\": an "
    "array of task objects, each with exactly these fields: "
    '"task_id" (short kebab-case string), "goal" (one concrete sentence), '
    '"parent_id" (task_id of the upstream task, or null), '
    '"capabilities" (array from: read_vault, llm_synthesis, write_file), '
    '"tools" (array of the same capability names), '
    '"expected_output" (string describing the deliverable), '
    '"verification_method" (one of: search_returned_hits, synthesis_nonempty, '
    'file_written, none), "timeout_s" (number), "retry_policy" (string like '
    '"retry:2"). The first task has parent_id null; later tasks may depend on '
    "earlier ones. No cycles. Return ONLY the JSON object — no fences, no "
    "commentary."
)

# Grounded verification methods the slice's verifier registry knows (T1.4).
_KNOWN_VERIFY = {
    "search_returned_hits",
    "synthesis_nonempty",
    "file_written",
    "file_written_with_heading",  # Phase 1 canonical task (vault note + heading)
    "none",
}
_KNOWN_CAPABILITIES = {"read_vault", "llm_synthesis", "write_file"}


# ---------------------------------------------------------------------------
# Deterministic template DAGs (the always-available fallback)
# ---------------------------------------------------------------------------

def template_dag(intent: Intent) -> TaskGraph:
    """Deterministic DAG for the research→synthesize→[write] slice shape.

    Shape is driven by the intent's requested permissions: a write_file
    permission adds the write task, otherwise the loop is read-only
    (research + synthesize, returning text). Deterministic: identical input
    always produces an identical graph.
    """
    requested = set(intent.permissions)

    research = Task(
        task_id="research",
        goal=(
            f"Search the vault for evidence on: {'; '.join(intent.goals)}"
            if intent.goals
            else f"Search the vault for evidence on: {intent.request}"
        ),
        required_capabilities=("read_vault",),
        tools=("search_query",),
        permissions=("read_vault",),
        expected_output="a list of vault sources with snippets, 1+ hits",
        verification_method="search_returned_hits",
        timeout_s=120.0,
        retry_policy="retry:2",
    )
    synthesize = Task(
        task_id="synthesize",
        goal="Synthesize the research into a concise client-ready brief",
        parent_id="research",
        inputs=({"from": "research", "kind": "sources"},),
        required_capabilities=("llm_synthesis",),
        tools=("chat",),
        permissions=("llm_synthesis",),
        expected_output="a non-empty brief derived from the sources",
        verification_method="synthesis_nonempty",
        timeout_s=120.0,
        retry_policy="retry:2",
    )
    tasks: List[Task] = [research, synthesize]

    if "write_file" in requested:
        tasks.append(
            Task(
                task_id="write",
                goal="Write the brief to a file",
                parent_id="synthesize",
                inputs=({"from": "synthesize", "kind": "brief"},),
                required_capabilities=("write_file",),
                tools=("vault_write",),
                permissions=("write_file",),
                expected_output="a non-empty file containing the brief, starting with a # heading",
                verification_method="file_written_with_heading",
                timeout_s=30.0,
                retry_policy="retry:1",
            )
        )

    return TaskGraph(goal=intent.request, tasks=tuple(tasks), source="template")


# ---------------------------------------------------------------------------
# LLM planner with tolerant parsing
# ---------------------------------------------------------------------------

def _parse_tasks(data: Dict[str, Any]) -> List[Task]:
    """Validate a model-provided tasks payload into Task objects.

    Invalid entries are dropped; an entry missing task_id or goal is invalid.
    Empty or invalid payloads yield an empty list, which the caller treats as
    a fallback trigger.
    """
    raw = data.get("tasks")
    if not isinstance(raw, list):
        return []
    parsed: List[Task] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        task_id = item.get("task_id")
        goal = item.get("goal")
        if not isinstance(task_id, str) or not task_id.strip():
            continue
        if not isinstance(goal, str) or not goal.strip():
            continue
        caps = _clean_str_list(item.get("capabilities"))
        caps = tuple(c for c in caps if c in _KNOWN_CAPABILITIES)
        tools = _clean_str_list(item.get("tools"))
        verify = item.get("verification_method")
        if verify not in _KNOWN_VERIFY:
            verify = "none"
        parent_id = item.get("parent_id")
        if parent_id is not None and not isinstance(parent_id, str):
            parent_id = None
        timeout_raw = item.get("timeout_s", 60.0)
        try:
            timeout_s = float(timeout_raw)
        except (TypeError, ValueError):
            timeout_s = 60.0
        if timeout_s <= 0:
            timeout_s = 60.0
        retry = item.get("retry_policy")
        parsed.append(
            Task(
                task_id=task_id.strip(),
                goal=goal.strip(),
                parent_id=parent_id,
                required_capabilities=caps,
                tools=tools,
                expected_output=str(item.get("expected_output", "") or ""),
                verification_method=verify,
                timeout_s=timeout_s,
                retry_policy=str(retry or "retry:2"),
            )
        )
    return parsed


def _is_acyclic_graph(tasks: List[Task]) -> bool:
    ids = {t.task_id for t in tasks}
    if len(ids) != len(tasks):
        return False  # duplicate task_id
    for t in tasks:
        if t.parent_id is not None and t.parent_id not in ids:
            return False  # dangling parent
    return TaskGraph(goal="", tasks=tuple(tasks), source="llm").is_acyclic()


def plan(
    intent: Intent,
    client: LocalAIClient | LlamaCPPClient | None = None,
) -> TaskGraph:
    """Build a task DAG for an Intent. LLM-first, template fallback."""
    if client is None:
        from msb_v3.local_ai.client_factory import get_client

        client = get_client()

    try:
        resp = client.generate(intent.request, system=_PLAN_SYSTEM, temperature=0.0, max_tokens=1024)
        data = _extract_json(resp.text)
        if data is not None:
            parsed = _parse_tasks(data)
            if parsed and _is_acyclic_graph(parsed):
                Metrics.inc("agentic", "plan:llm")
                return TaskGraph(goal=intent.request, tasks=tuple(parsed), source="llm")
    except Exception:
        pass

    Metrics.inc("agentic", "plan:template")
    return template_dag(intent)
