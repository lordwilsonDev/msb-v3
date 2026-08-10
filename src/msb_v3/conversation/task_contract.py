"""Task Contract v1 — dag-node contract validation, caps, predicate registry.

Implements docs/task-contract-v1.md: workflow-mode dag entries become
machine-readable job specifications. The discriminator is pinned: `task_id`
present ⇒ full contract validation; absent ⇒ the legacy `{skill, args}`
unverified form (allowed, but its claim can never reach VERIFIED).

Everything here is deterministic and zero-spend — no model, no network.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Callable, Optional

from msb_v3.conversation.envelope import canonical_json

# --- caps (spec §7 — enforced at validation, not by convention) ---
# The static graph caps are enforced here. The executor-side caps
# (budget_cap_usd / max_steps / stall_threshold) are sanity-checked here but
# ENFORCED at runtime by the executor slice (their defaults live there, where
# they are used — no dead constants in the validator).

MAX_DAG_DEPTH = 3
MAX_DAG_NODES = 50
MAX_NODES_PER_LEVEL = 20

VALID_STATUSES = ("READY", "RUNNING", "SUBMITTED", "VERIFIED", "FAILED", "ROLLED_BACK")
VALID_VERIFICATION_MODES = ("on_submit", "external")
VALID_SIDE_EFFECT_KINDS = ("file_write",)
VALID_ROLLBACK_KINDS = ("git_revert", "file_delete")
VALID_CONSTRAINT_KEYS = ("budget_cap_usd", "max_steps", "stall_threshold")

CONTRACT_FIELDS = frozenset({
    "task_id", "objective", "skill", "args", "inputs", "allowed_tools",
    "allowed_data", "constraints", "preconditions", "expected_output",
    "verification", "side_effects", "rollback", "confidence", "parent", "status",
})
LEGACY_FIELDS = frozenset({"skill", "args"})

# --- claim derivation (pinned to the replay consumer's derivation) ---


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def claim_ok_task(task_id: str) -> str:
    """claim:ok:task:<task_id> — BYTE-IDENTICAL to the replay consumer's
    derivation (subject=f'task:{task_id}', claim_id=f'claim:ok:{subject}').
    One availability claim across producers; a TASK_FAILED event carrying the
    same task_id collides on this id and REGRESSES it."""
    return f"claim:ok:task:{task_id}"


def claim_done_task(
    task_id: str,
    expected_output: Any,
    predicates: Any,
) -> str:
    """claim:done:task:<hash> — epistemic: is the task's declared output, as
    specified, actually produced? Content-addressed on the CONTRACT (task_id +
    expected_output + predicates), never on execution state."""
    base = canonical_json({
        "task_id": task_id,
        "expected_output": expected_output or {},
        "predicates": predicates or [],
    })
    return f"claim:done:task:{_sha256(base)[:12]}"


# --- predicate registry (spec §4 — the ONLY verification surface) ---

Predicate = dict[str, Any]
PredicateOutcome = dict[str, Any]

# kind -> required arg types. `expected` accepts anything.
_PREDICATE_ARGS: dict[str, dict[str, tuple[type, ...]]] = {
    "exit_code": {"code": (int,)},
    "file_exists": {"path": (str,)},
    "file_contains": {"path": (str,), "text": (str,)},
    "file_not_contains": {"path": (str,), "text": (str,)},
    "output_equals": {"expected": (object,)},
    "artifact_hash": {"path": (str,), "sha256": (str,)},
}


def _scoped_path(output_root: Path, rel: str) -> Path:
    """Resolve a predicate path INSIDE the task's declared output scope.
    Traversal outside the scope raises (a predicate that escapes its scope is
    a failed predicate + a flagged contract, never a read of foreign files)."""
    base = output_root.resolve()
    target = (base / rel).resolve()
    if os.path.commonpath([str(base), str(target)]) != str(base):
        raise ValueError(f"path escapes output scope: {rel!r}")
    return target


def _run_exit_code(pred: Predicate, root: Path, result: dict) -> tuple[bool, str]:
    got = result.get("exit_code")
    want = pred["code"]
    return got == want, f"exit_code {got!r} == {want!r}" if got == want else f"exit_code {got!r} != {want!r}"


def _run_file_exists(pred: Predicate, root: Path, result: dict) -> tuple[bool, str]:
    p = _scoped_path(root, pred["path"])
    return p.exists(), f"{pred['path']} exists" if p.exists() else f"{pred['path']} missing"


def _run_file_contains(pred: Predicate, root: Path, result: dict) -> tuple[bool, str]:
    p = _scoped_path(root, pred["path"])
    if not p.exists():
        return False, f"{pred['path']} missing"
    content = p.read_text(encoding="utf-8", errors="replace")
    hit = pred["text"] in content
    return hit, f"{pred['path']} contains text" if hit else f"{pred['path']} missing text"


def _run_file_not_contains(pred: Predicate, root: Path, result: dict) -> tuple[bool, str]:
    p = _scoped_path(root, pred["path"])
    if not p.exists():
        return True, f"{pred['path']} absent (nothing to scan)"
    content = p.read_text(encoding="utf-8", errors="replace")
    hit = pred["text"] in content
    return not hit, f"{pred['path']} clear of text" if not hit else f"{pred['path']} contains forbidden text"


def _run_output_equals(pred: Predicate, root: Path, result: dict) -> tuple[bool, str]:
    got = canonical_json(result.get("output"))
    want = canonical_json(pred["expected"])
    return got == want, "output equals expected" if got == want else "output differs from expected"


def _run_artifact_hash(pred: Predicate, root: Path, result: dict) -> tuple[bool, str]:
    p = _scoped_path(root, pred["path"])
    if not p.exists():
        return False, f"{pred['path']} missing"
    actual = hashlib.sha256(p.read_bytes()).hexdigest()
    want = pred["sha256"].lower()
    return actual == want, "artifact hash matches" if actual == want else f"hash {actual[:12]}… != {want[:12]}…"


_PREDICATE_RUNNERS: dict[str, Callable[[Predicate, Path, dict], tuple[bool, str]]] = {
    "exit_code": _run_exit_code,
    "file_exists": _run_file_exists,
    "file_contains": _run_file_contains,
    "file_not_contains": _run_file_not_contains,
    "output_equals": _run_output_equals,
    "artifact_hash": _run_artifact_hash,
}


def _validate_predicates(predicates: Any) -> list[str]:
    """Every predicate must be a known kind with the required args — the
    registry is the ONLY verification surface; no eval, no custom code."""
    errors: list[str] = []
    if not isinstance(predicates, list):
        return ["expected_output.predicates must be a list"]
    for i, pred in enumerate(predicates):
        if not isinstance(pred, dict):
            errors.append(f"predicate {i} must be an object")
            continue
        kind = pred.get("kind")
        if not isinstance(kind, str) or kind not in _PREDICATE_ARGS:
            errors.append(f"predicate {i}: unknown kind {kind!r} (registry: {sorted(_PREDICATE_ARGS)})")
            continue
        for arg, types in _PREDICATE_ARGS[kind].items():
            if arg not in pred:
                errors.append(f"predicate {i} ({kind}): missing required arg {arg!r}")
            elif types != (object,) and not isinstance(pred[arg], types):
                errors.append(f"predicate {i} ({kind}): arg {arg!r} must be {types[0].__name__}")
        for arg in pred:
            if arg not in ("kind", *_PREDICATE_ARGS[kind]):
                errors.append(f"predicate {i} ({kind}): unknown arg {arg!r}")
    return errors


# --- contract validation (spec §2) ---


def validate_contract(entry: Any, dag_task_ids: set[str]) -> list[str]:
    """Validate one dag entry. `task_id` present ⇒ full contract validation;
    absent ⇒ the legacy `{skill, args}` unverified form (allowed)."""
    errors: list[str] = []
    if not isinstance(entry, dict):
        return ["dag entry must be an object"]
    if "task_id" not in entry:
        unknown = set(entry) - LEGACY_FIELDS
        if unknown:
            errors.append(f"legacy dag entry: unknown fields {sorted(unknown)} (only 'skill' and 'args' allowed)")
        if not isinstance(entry.get("skill"), str) or not entry.get("skill"):
            errors.append("legacy dag entry requires a non-empty 'skill'")
        return errors

    unknown = set(entry) - CONTRACT_FIELDS
    if unknown:
        errors.append(f"task {entry['task_id']!r}: unknown contract fields {sorted(unknown)}")

    task_id = entry.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        errors.append("task_id must be a non-empty string")
        return errors
    for field in ("objective", "skill"):
        value = entry.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"task {task_id!r}: {field} must be a non-empty string")
    if entry.get("args") is not None and not isinstance(entry["args"], dict):
        errors.append(f"task {task_id!r}: args must be an object")

    # inputs: no self/forward references. An input is a task output ref
    # ("task_id" or "task_id:<output>") — the TASK part must be a known,
    # earlier task, never the task itself.
    inputs = entry.get("inputs", [])
    if not isinstance(inputs, list) or not all(isinstance(i, str) for i in inputs):
        errors.append(f"task {task_id!r}: inputs must be a list of strings")
    else:
        for i in inputs:
            task_part = i.split(":", 1)[0]
            if not task_part:
                errors.append(f"task {task_id!r}: input {i!r} has no task reference")
            elif task_part == task_id:
                errors.append(f"task {task_id!r}: input cannot reference itself")
            elif task_part not in dag_task_ids:
                errors.append(f"task {task_id!r}: input {i!r} references an unknown task")

    # permission envelope
    for field in ("allowed_tools", "allowed_data"):
        value = entry.get(field, [])
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            errors.append(f"task {task_id!r}: {field} must be a list of strings")

    # constraints (ralph-loop mirror)
    constraints = entry.get("constraints")
    if constraints is not None:
        if not isinstance(constraints, dict):
            errors.append(f"task {task_id!r}: constraints must be an object")
        else:
            for key in constraints:
                if key not in VALID_CONSTRAINT_KEYS:
                    errors.append(f"task {task_id!r}: unknown constraint {key!r}")
            for key in VALID_CONSTRAINT_KEYS:
                if key in constraints and not isinstance(constraints[key], (int, float)):
                    errors.append(f"task {task_id!r}: constraint {key} must be numeric")
            if isinstance(constraints.get("budget_cap_usd"), (int, float)) and constraints["budget_cap_usd"] < 0:
                errors.append(f"task {task_id!r}: budget_cap_usd cannot be negative")
            if isinstance(constraints.get("max_steps"), int) and constraints["max_steps"] < 1:
                errors.append(f"task {task_id!r}: max_steps must be >= 1")

    # preconditions: task_id:VERIFIED pairs, known tasks
    preconditions = entry.get("preconditions", [])
    if not isinstance(preconditions, list) or not all(isinstance(p, str) for p in preconditions):
        errors.append(f"task {task_id!r}: preconditions must be a list of 'task_id:VERIFIED' strings")
    else:
        for p in preconditions:
            if ":" not in p:
                errors.append(f"task {task_id!r}: precondition {p!r} must be 'task_id:VERIFIED'")
                continue
            task_part, status_part = p.rsplit(":", 1)
            if task_part not in dag_task_ids:
                errors.append(f"task {task_id!r}: precondition references unknown task {task_part!r}")
            if status_part != "VERIFIED":
                errors.append(f"task {task_id!r}: precondition {p!r} must require VERIFIED status")

    # expected_output: schema + predicates; VERIFIED requires a strong predicate
    expected_output = entry.get("expected_output")
    if expected_output is not None:
        if not isinstance(expected_output, dict):
            errors.append(f"task {task_id!r}: expected_output must be an object")
        else:
            if "schema" in expected_output and not isinstance(expected_output["schema"], dict):
                errors.append(f"task {task_id!r}: expected_output.schema must be an object")
            preds = expected_output.get("predicates", [])
            errors += [f"task {task_id!r}: {e}" for e in _validate_predicates(preds)]
            if isinstance(preds, list) and preds and not any(
                isinstance(p, dict) and p.get("kind") != "exit_code" for p in preds
            ):
                errors.append(
                    f"task {task_id!r}: expected_output must include >= 1 predicate stronger than exit_code"
                )
    verification = entry.get("verification", "on_submit")
    if verification not in VALID_VERIFICATION_MODES:
        errors.append(f"task {task_id!r}: verification must be on_submit or external")

    # side effects + rollback
    side_effects = entry.get("side_effects", [])
    if not isinstance(side_effects, list):
        errors.append(f"task {task_id!r}: side_effects must be a list")
    else:
        for s in side_effects:
            if not isinstance(s, dict):
                errors.append(f"task {task_id!r}: side effect must be an object")
                continue
            if s.get("kind") not in VALID_SIDE_EFFECT_KINDS:
                errors.append(f"task {task_id!r}: unknown side-effect kind {s.get('kind')!r}")
            if not isinstance(s.get("path"), str):
                errors.append(f"task {task_id!r}: side effect requires a string path")
    rollback = entry.get("rollback")
    if rollback is not None:
        if not isinstance(rollback, dict):
            errors.append(f"task {task_id!r}: rollback must be an object")
        elif rollback.get("kind") not in VALID_ROLLBACK_KINDS:
            errors.append(f"task {task_id!r}: unknown rollback kind {rollback.get('kind')!r}")
        elif not isinstance(rollback.get("scope"), str):
            errors.append(f"task {task_id!r}: rollback requires a string scope")

    # confidence: evidence, never a verdict input
    confidence = entry.get("confidence")
    if confidence is not None and not (isinstance(confidence, (int, float)) and 0 <= confidence <= 1):
        errors.append(f"task {task_id!r}: confidence must be a number in [0, 1]")

    parent = entry.get("parent")
    if parent is not None and (not isinstance(parent, str) or parent not in dag_task_ids):
        errors.append(f"task {task_id!r}: parent {parent!r} must reference a known task_id")

    status = entry.get("status", "READY")
    if status not in VALID_STATUSES:
        errors.append(f"task {task_id!r}: invalid status {status!r}")

    return errors


# --- dag caps (spec §7 — the graph-explosion governor) ---


def _depth_of(task_id: str, parent_map: dict[str, Optional[str]]) -> int:
    """Longest parent chain depth; a parent cycle is treated as unbounded
    (returns MAX_DAG_DEPTH + 1 so the cap fires)."""
    depth, seen = 0, set()
    cur: Optional[str] = task_id
    while cur is not None:
        if cur in seen:
            return MAX_DAG_DEPTH + 1  # cycle
        seen.add(cur)
        depth += 1
        cur = parent_map.get(cur)
    return depth


def validate_dag(dag: Any) -> list[str]:
    """Validate the whole dag: node caps, unique ids, per-entry contracts,
    depth, per-level caps. Returns errors (empty = valid)."""
    errors: list[str] = []
    if not isinstance(dag, list):
        return ["workflow.dag must be a list"]
    if len(dag) > MAX_DAG_NODES:
        errors.append(f"dag exceeds max_dag_nodes={MAX_DAG_NODES}")

    task_ids: list[str] = []
    for entry in dag:
        if isinstance(entry, dict) and isinstance(entry.get("task_id"), str) and entry["task_id"].strip():
            task_ids.append(entry["task_id"])
    seen: set[str] = set()
    for tid in task_ids:
        if tid in seen:
            errors.append(f"duplicate task_id {tid!r}")
        seen.add(tid)
    id_set = set(task_ids)

    for entry in dag:
        errors += validate_contract(entry, id_set)

    if task_ids:
        parent_map: dict[str, Optional[str]] = {
            e["task_id"]: e.get("parent") for e in dag
            if isinstance(e, dict) and isinstance(e.get("task_id"), str) and "task_id" in e
        }
        for tid in task_ids:
            if _depth_of(tid, parent_map) > MAX_DAG_DEPTH:
                errors.append(f"task {tid!r}: parent chain exceeds max_dag_depth={MAX_DAG_DEPTH}")
        # per-level: siblings share a direct parent (or the root level)
        counts: dict[str, int] = {}
        for tid in task_ids:
            key = parent_map.get(tid) or "<root>"
            counts[key] = counts.get(key, 0) + 1
        for key, count in counts.items():
            if count > MAX_NODES_PER_LEVEL:
                errors.append(f"level {key!r} exceeds max_nodes_per_level={MAX_NODES_PER_LEVEL}")
    return errors


def validate_workflow(workflow: Any) -> list[str]:
    """Validate the envelope's workflow block (goal + dag + step_tracker).
    The endpoint maps a non-empty result to 422 contract_invalid."""
    if not isinstance(workflow, dict):
        return ["workflow must be an object"]
    errors: list[str] = []
    goal = workflow.get("goal")
    if not isinstance(goal, str) or not goal.strip():
        errors.append("workflow.goal must be a non-empty string")
    errors += validate_dag(workflow.get("dag", []))
    step_tracker = workflow.get("step_tracker")
    if step_tracker is not None:
        if not isinstance(step_tracker, dict):
            errors.append("workflow.step_tracker must be an object")
        elif "required_steps" in step_tracker and (
            not isinstance(step_tracker["required_steps"], list)
            or not all(isinstance(x, str) for x in step_tracker["required_steps"])
        ):
            errors.append("workflow.step_tracker.required_steps must be a list of strings")
    return errors


# --- predicate runner (spec §4 — deterministic, zero-spend) ---


def run_predicates(
    predicates: list[dict[str, Any]],
    output_root: Path,
    result: dict[str, Any],
) -> list[PredicateOutcome]:
    """Run each predicate against the submitted task output. `result` is the
    executor's submission ({exit_code, output}); `output_root` is the task's
    declared output scope. Returns one outcome per predicate — a predicate
    failure is a FAILED outcome, never a crash."""
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    outcomes: list[PredicateOutcome] = []
    for pred in predicates:
        kind = pred.get("kind")
        if not isinstance(kind, str):
            outcomes.append({"kind": kind, "passed": False, "detail": f"unknown predicate kind {kind!r}"})
            continue
        runner = _PREDICATE_RUNNERS.get(kind)
        if runner is None:
            outcomes.append({"kind": kind, "passed": False, "detail": f"unknown predicate kind {kind!r}"})
            continue
        try:
            passed, detail = runner(pred, root, result)
            outcomes.append({"kind": kind, "passed": bool(passed), "detail": detail})
        except (ValueError, OSError, KeyError, TypeError) as exc:
            # run_predicates is a public API — a predicate failure is a FAILED
            # outcome, never a crash (a malformed predicate must not kill the
            # whole verification run).
            outcomes.append({"kind": kind, "passed": False, "detail": str(exc)})
    return outcomes


def predicates_pass(outcomes: list[PredicateOutcome]) -> bool:
    return all(o.get("passed") for o in outcomes)
