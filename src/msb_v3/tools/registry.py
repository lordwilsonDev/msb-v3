"""Canonical tool registry (unified-architecture §6).

One place that answers, for every tool the model may call:

    tool_id / name / description / input_schema / risk_class /
    mutation_class / required_capabilities / approval_required

The model never receives unrestricted capabilities — the harness only
advertises what the perimeter can back (``runtime.register_governed_tools``
skips anything not in ``TOOLS``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

# Risk / mutation vocabularies (mirror the unified-architecture §6 table).
RISK_LOW = "LOW"
RISK_MEDIUM = "MEDIUM"
RISK_HIGH = "HIGH"
MUTATION_NONE = "NONE"
MUTATION_WRITE = "WRITE"
MUTATION_SYSTEM = "SYSTEM"


@dataclass(frozen=True)
class ToolDef:
    tool_id: str
    description: str
    parameters: Dict[str, Any]  # JSON schema for the tool's arguments
    risk_class: str
    mutation_class: str
    required_capabilities: tuple[str, ...] = ()
    approval_required: bool = False

    def as_model_schema(self) -> Dict[str, Any]:
        """The OpenAI-function shape the model sees (what /chat advertises)."""
        return {
            "type": "function",
            "name": self.tool_id,
            "description": self.description,
            "parameters": self.parameters,
        }


TOOLS: Dict[str, ToolDef] = {
    "search_vault": ToolDef(
        tool_id="search_vault",
        description=(
            "Semantic search over the current tenant's vault (Qdrant RAG). "
            "Returns the top matching passages with their sources."
        ),
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "the search query"}},
            "required": ["query"],
        },
        risk_class=RISK_LOW,
        mutation_class=MUTATION_NONE,
    ),
    "vault_read": ToolDef(
        tool_id="vault_read",
        description=(
            "Read a text file inside the vault root. Path is relative to the "
            "vault; traversal outside the vault is denied."
        ),
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string", "description": "path relative to the vault root"}},
            "required": ["path"],
        },
        risk_class=RISK_LOW,
        mutation_class=MUTATION_NONE,
    ),
    "vault_write": ToolDef(
        tool_id="vault_write",
        description=(
            "Write a text file inside the vault root (path relative to the "
            "vault). This mutates the vault and requires the 'vault.write' "
            "capability — denied by default."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "path relative to the vault root"},
                "content": {"type": "string", "description": "file content"},
            },
            "required": ["path", "content"],
        },
        risk_class=RISK_MEDIUM,
        mutation_class=MUTATION_WRITE,
        required_capabilities=("vault.write",),
    ),
    "vault_append": ToolDef(
        tool_id="vault_append",
        description=(
            "Append text to a file inside the vault root (path relative to the "
            "vault). Creates the file if missing. Mutates the vault and "
            "requires the 'vault.write' capability — denied by default."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "path relative to the vault root"},
                "content": {"type": "string", "description": "text to append"},
            },
            "required": ["path", "content"],
        },
        risk_class=RISK_MEDIUM,
        mutation_class=MUTATION_WRITE,
        required_capabilities=("vault.write",),
    ),
    "vault_patch": ToolDef(
        tool_id="vault_patch",
        description=(
            "Patch a file inside the vault root with a replace or regex "
            "operation (path relative to the vault). Mutates the vault and "
            "requires the 'vault.write' capability — denied by default."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "path relative to the vault root"},
                "operation": {
                    "type": "string",
                    "enum": ["replace", "regex"],
                    "description": "replace (literal) or regex substitution",
                },
                "target": {"type": "string", "description": "pattern to find"},
                "content": {"type": "string", "description": "replacement text"},
            },
            "required": ["path"],
        },
        risk_class=RISK_MEDIUM,
        mutation_class=MUTATION_WRITE,
        required_capabilities=("vault.write",),
    ),
    "vault_delete": ToolDef(
        tool_id="vault_delete",
        description=(
            "Delete a file inside the vault root (path relative to the vault). "
            "Destructive. Mutates the vault and requires the 'vault.write' "
            "capability — denied by default."
        ),
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string", "description": "path relative to the vault root"}},
            "required": ["path"],
        },
        risk_class=RISK_MEDIUM,
        mutation_class=MUTATION_WRITE,
        required_capabilities=("vault.write",),
    ),
    "vault_move": ToolDef(
        tool_id="vault_move",
        description=(
            "Move/rename a file inside the vault root (paths relative to the "
            "vault). Mutates the vault and requires the 'vault.write' "
            "capability — denied by default."
        ),
        parameters={
            "type": "object",
            "properties": {
                "from_path": {"type": "string", "description": "source path relative to the vault root"},
                "to_path": {"type": "string", "description": "destination path relative to the vault root"},
            },
            "required": ["from_path", "to_path"],
        },
        risk_class=RISK_MEDIUM,
        mutation_class=MUTATION_WRITE,
        required_capabilities=("vault.write",),
    ),
    # --- Code Graph (sovereign-architecture §4.2.1, P0) ---
    # Repository intelligence: all read-only over the local SQLite graph.
    # The model never touches source directly — it queries the index, which
    # is what keeps these <1s and inside the perimeter.
    "codegraph.explore": ToolDef(
        tool_id="codegraph.explore",
        description=(
            "Search a repository's symbol index (Code Graph). Returns "
            "functions/classes/methods matching a name with file/line "
            "locations. Pass repo (the path or key it was indexed under) "
            "and name."
        ),
        parameters={
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "repository path or index key"},
                "name": {"type": "string", "description": "symbol name to search for"},
            },
            "required": ["repo", "name"],
        },
        risk_class=RISK_LOW,
        mutation_class=MUTATION_NONE,
    ),
    "codegraph.context": ToolDef(
        tool_id="codegraph.context",
        description=(
            "Get one symbol's context: its definition (file/line/signature) "
            "plus who calls it and what it calls — enough to reason about it "
            "without loading the whole file."
        ),
        parameters={
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "repository path or index key"},
                "symbol": {"type": "string", "description": "fully-qualified or short symbol name"},
            },
            "required": ["repo", "symbol"],
        },
        risk_class=RISK_LOW,
        mutation_class=MUTATION_NONE,
    ),
    "codegraph.impact": ToolDef(
        tool_id="codegraph.impact",
        description=(
            "Blast-radius analysis: given a file (optionally a line), list "
            "every symbol that change would affect, including transitive "
            "callers. Use before editing code."
        ),
        parameters={
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "repository path or index key"},
                "file": {"type": "string", "description": "file path relative to the repo"},
                "line": {"type": "integer", "description": "optional line within the file"},
            },
            "required": ["repo", "file"],
        },
        risk_class=RISK_LOW,
        mutation_class=MUTATION_NONE,
    ),
    "codegraph.rename": ToolDef(
        tool_id="codegraph.rename",
        description=(
            "Rename preview: every reference (definitions, calls, imports) "
            "that renaming a symbol would touch. Read-only — it never "
            "mutates; use it to scope a rename before doing it."
        ),
        parameters={
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "repository path or index key"},
                "name": {"type": "string", "description": "symbol name to preview a rename of"},
            },
            "required": ["repo", "name"],
        },
        risk_class=RISK_LOW,
        mutation_class=MUTATION_NONE,
    ),
    # --- Memory Fabric (sovereign-architecture §4.2.2, P0) ---
    # Durable cross-session agent memory with provenance and verification
    # states. recall is read-only; store mutates the fabric and requires
    # the 'memory.write' capability — denied by default.
    "memory.recall": ToolDef(
        tool_id="memory.recall",
        description=(
            "Recall memories from the fabric: rank relevant memories for a "
            "query (keyword + optional semantic boost), filtered by project "
            "/tech / type. Returns scored memories with their verification "
            "states — use before answering to honor prior decisions."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "what to recall"},
                "project": {"type": "string", "description": "optional project filter"},
                "tech": {"type": "string", "description": "optional tech filter"},
                "type": {"type": "string", "description": "optional memory type: episodic|semantic|procedural|architectural"},
                "top_k": {"type": "integer", "description": "max results (default 8)"},
            },
            "required": ["query"],
        },
        risk_class=RISK_LOW,
        mutation_class=MUTATION_NONE,
    ),
    "memory.store": ToolDef(
        tool_id="memory.store",
        description=(
            "Store a memory in the fabric for future sessions: type, content, "
            "tags, importance, source, project/tech context. Mutates the "
            "fabric and requires the 'memory.write' capability — denied by "
            "default. New memories start UNVERIFIED."
        ),
        parameters={
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "the memory content"},
                "type": {"type": "string", "description": "episodic|semantic|procedural|architectural (default semantic)"},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "optional tags"},
                "importance": {"type": "number", "description": "0.0-1.0 (default 0.5)"},
                "source_agent": {"type": "string", "description": "which agent/session produced it"},
                "project": {"type": "string", "description": "optional project context"},
                "tech": {"type": "string", "description": "optional tech context"},
            },
            "required": ["content"],
        },
        risk_class=RISK_MEDIUM,
        mutation_class=MUTATION_WRITE,
        required_capabilities=("memory.write",),
    ),
    # --- Context Engine (sovereign-architecture §4.2.3, P1) ---
    # Compose a layered, budgeted context (L0-L7) for a task. Read-only —
    # it curates what the model sees, it does not mutate anything.
    "context.compose": ToolDef(
        tool_id="context.compose",
        description=(
            "Compose a layered context for a task (system invariants, task, "
            "repo structure, relevant code, memories, skills, history). "
            "Returns a token-budgeted context string + a per-layer ledger "
            "showing what fit and what was evicted — use before answering "
            "to avoid dumping whole files into the prompt."
        ),
        parameters={
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "the task to compose context for"},
                "repo": {"type": "string", "description": "optional repo key (code graph layers)"},
                "project": {"type": "string", "description": "optional project filter for memories"},
                "tech": {"type": "string", "description": "optional tech filter for memories"},
                "budget_tokens": {"type": "integer", "description": "hard token budget (default 4000)"},
            },
            "required": ["task"],
        },
        risk_class=RISK_LOW,
        mutation_class=MUTATION_NONE,
    ),
    # --- MoIE (sovereign-architecture §3, §23-25; Phase 3) ---
    # Mixture of Inversion Experts: route a claim through adversarial
    # experts, merge evidence, detect contradictions, and produce a
    # fail-closed decision with an Inversion Depth Score. Read-only — it
    # inverts assumptions, it does not execute anything.
    "moie.analyze": ToolDef(
        tool_id="moie.analyze",
        description=(
            "Run Mixture-of-Inversion-Experts on a consequential claim: "
            "extract + invert its assumptions, retrieve grounding evidence, "
            "detect expert contradictions, and return a fail-closed verdict "
            "(APPROVE / CONDITIONAL / BLOCK) with an Inversion Depth Score "
            "— use before executing a high-impact action to surface what "
            "the plan is assuming without question."
        ),
        parameters={
            "type": "object",
            "properties": {
                "claim": {"type": "string", "description": "the claim / plan to invert"},
                "domains": {"type": "array", "items": {"type": "string"}, "description": "force specific experts (security, architecture, economic, operational, reliability, governance, adversarial, human-factor, data-memory, domain)"},
                "thorough": {"type": "boolean", "description": "run every expert (default: safety trio + keyword matches)"},
                "high_impact": {"type": "boolean", "description": "escalate concerns toward BLOCK (default false)"},
                "tenant": {"type": "string", "description": "tenant for evidence retrieval (default default)"},
            },
            "required": ["claim"],
        },
        risk_class=RISK_LOW,
        mutation_class=MUTATION_NONE,
    ),
    # --- Software Factory (sovereign-architecture §4.2.6, P3) ---
    # Issue → classify → plan → build (isolated worktree) → test →
    # independent review → grounded verification → verdict. This MUTATES
    # (a worktree copy + builder), so it is MEDIUM/WRITE and requires the
    # factory.run capability — denied by default, like vault_write.
    "factory.run": ToolDef(
        tool_id="factory.run",
        description=(
            "Run the Software Factory on an issue: classify, plan (with MoIE "
            "risks), build in an isolated worktree (never the original "
            "repo), run the repo's tests, independently review (MoIE + "
            "code graph), and verify acceptance criteria against real "
            "evidence. Returns a verdict (MERGED / NEEDS_WORK / BLOCKED / "
            "FAILED) with a hash evidence chain. No agent certifies its "
            "own work — the review and verification are independent."
        ),
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "issue title"},
                "body": {"type": "string", "description": "issue body (optional)"},
                "repo": {"type": "string", "description": "path to the repo to change (read-only source; a copy is built)"},
                "labels": {"type": "array", "items": {"type": "string"}, "description": "issue labels (optional)"},
                "builder": {"type": "string", "description": "cli (agent worker, default) or patch (deterministic patch script)"},
                "patch_script": {"type": "string", "description": "path to the patch script when builder=patch"},
            },
            "required": ["title", "repo"],
        },
        risk_class=RISK_MEDIUM,
        mutation_class=MUTATION_WRITE,
        required_capabilities=("factory.run",),
    ),
}


def model_schemas(tool_ids: List[str]) -> List[Dict[str, Any]]:
    """Model-facing schemas for a subset of registry tools (order preserved)."""
    return [TOOLS[t].as_model_schema() for t in tool_ids if t in TOOLS]
