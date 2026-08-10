"""Task Contract Executor — runs a dag under the contract's permission envelope.

Implements docs/task-contract-v1.md §3–§8. The executor is deterministic and
zero-spend: the skill's actual work is a pluggable `runner` hook (in CI the
StubRunner; in production the domain-router dispatch). Everything the executor
does itself — permission checks, executor-side caps, predicate verification,
rollback confirmation, ledger emission — is stdlib-only code, never a model.

The central invariant is enforced here, not asserted: **exit 0 ≠ VERIFIED**.
A task that exits 0 with failing predicates is FAILED and the ledger records
it as such; a task with no expected_output reaches at most SUBMITTED and its
claim can never be VERIFIED.

Execution granularity (pinned): v1 executes exactly ONE contract per call —
`advance_dag` selects one READY task whose preconditions hold, executes it,
and returns the dag with that task's status advanced.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from msb_v3.conversation import task_producer
from msb_v3.conversation.envelope import now_iso
from msb_v3.conversation.task_contract import (
    predicates_pass,
    run_predicates,
    validate_contract,
)

# Executor-side cap defaults (spec §7 — enforced here, where they are used).
DEFAULT_BUDGET_CAP_USD = 1.00
DEFAULT_MAX_STEPS = 12

Runner = Callable[[dict[str, Any]], dict[str, Any]]


class ToolBroker:
    """§6 permission envelope — fail-closed, checked on every reported access.

    - `allowed_tools` absent ⇒ [] ⇒ no tools at all (deterministic-function
      tasks only).
    - `allowed_data` absent ⇒ the request tenant only (the executor computes
      the effective scope before calling).
    - Side-effect scope is a guardrail, not documentation: an artifact outside
      the declared `side_effects` paths is a contract violation.
    """

    def authorize_tool(self, allowed_tools: list[str], tool: str) -> bool:
        return tool in allowed_tools

    def authorize_data(self, data_scope: list[str], tenant: str) -> bool:
        return tenant in data_scope

    def side_effect_violations(
        self, declared: list[dict], artifacts: list[str], output_root: Path,
    ) -> list[str]:
        if not declared:
            return (
                [f"artifact {a!r} outside declared side effects (none declared — fail closed)" for a in artifacts]
                if artifacts else []
            )
        base = output_root.resolve()
        allowed_dirs: list[Path] = []
        for effect in declared:
            raw = str(effect.get("path", ""))
            # An empty declaration must never silently mean "the whole root"
            # — fail closed (reviewer finding: `"path": ""` used to widen the
            # envelope to everything). A bare "**" / "." is a DELIBERATE
            # whole-root declaration — bounded to the task's own output root,
            # never outside it.
            if not raw.strip():
                return ["side-effect declaration has an empty path — name a subpath"]
            if raw.startswith("/") or os.path.isabs(raw):
                # an absolute path would survive normalization ("/".rstrip("/")
                # -> "" -> ".") and silently become whole-root — reject it
                return [f"side-effect declaration {raw!r} is absolute — name a path inside the output root"]
            # normalize glob-ish declarations ("out/**" -> "out")
            norm = raw.split("**", 1)[0].rstrip("/") or "."
            d = (base / norm).resolve()
            try:
                inside = os.path.commonpath([str(base), str(d)]) == str(base)
            except ValueError:
                inside = False
            if not inside:
                return [f"side-effect declaration {raw!r} resolves outside the output root"]
            allowed_dirs.append(d)
        violations: list[str] = []
        for art in artifacts:
            target = (base / art).resolve()
            within = False
            for d in allowed_dirs:
                try:
                    if os.path.commonpath([str(d), str(target)]) == str(d):
                        within = True
                        break
                except ValueError:
                    continue
            if not within:
                violations.append(f"artifact {art!r} outside declared side-effect scope")
        return violations


def select_ready_task(dag: list[dict]) -> Optional[dict]:
    """Spec §9.2 — the router's selection: the FIRST task with status READY
    whose preconditions are all satisfied (each `task_id:VERIFIED`
    precondition has a dag entry with status VERIFIED). Returns None when
    nothing is ready. The only state a router may select is READY."""
    verified = {
        e.get("task_id") for e in dag
        if isinstance(e, dict) and e.get("status") == "VERIFIED"
    }
    for entry in dag:
        if not isinstance(entry, dict) or entry.get("status") != "READY":
            continue
        preconditions = entry.get("preconditions") or []
        if all(p.rsplit(":", 1)[0] in verified for p in preconditions):
            return entry
    return None


def _run_rollback(
    rollback: dict,
    artifacts: list[str],
    output_root: Path,
    pre_task_head: Optional[str],
) -> tuple[bool, str]:
    """Execute the declared restore plan and CONFIRM it (spec §5 — confirmation
    is not aspirational). Returns (confirmed, detail). A rollback whose
    confirmation fails is an incident — never a false ROLLED_BACK."""
    kind = rollback.get("kind")
    scope = str(rollback.get("scope", ""))
    base = Path(output_root).resolve()
    if kind == "file_delete":
        removed = 0
        for art in artifacts:
            p = (base / art).resolve()
            try:
                if os.path.commonpath([str(base), str(p)]) != str(base):
                    continue  # never delete outside the output root
            except ValueError:
                continue
            if p.exists():
                if p.is_dir():
                    return False, f"file_delete cannot confirm: {art!r} is a directory"
                p.unlink()
                removed += 1
        remaining = [a for a in artifacts if (base / a).exists()]
        if remaining:
            return False, f"file_delete confirmation failed — still present: {remaining}"
        return True, f"file_delete confirmed ({removed} removed)"
    if kind == "git_revert":
        if not pre_task_head:
            return False, "git_revert confirmation failed — no pre-task HEAD recorded"
        try:
            # RESTORE first (the declared restore plan: workspace back to the
            # pre-task HEAD), then CONFIRM (spec §5 — confirmation is not
            # aspirational).
            reset = subprocess.run(
                ["git", "-C", str(base), "reset", "--hard", pre_task_head],
                capture_output=True, text=True, timeout=10,
            )
            if reset.returncode != 0:
                return False, f"git_revert restore failed: {reset.stderr.strip()[:120]}"
            head = subprocess.run(
                ["git", "-C", str(base), "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=10,
            )
            if head.returncode != 0 or head.stdout.strip() != pre_task_head:
                return False, f"git_revert confirmation failed — HEAD {head.stdout.strip()[:12] or '?'} != pre-task {pre_task_head[:12]}"
            status = subprocess.run(
                ["git", "-C", str(base), "status", "--porcelain", "--", scope],
                capture_output=True, text=True, timeout=10,
            )
            if status.returncode != 0 or status.stdout.strip():
                return False, "git_revert confirmation failed — scope not clean after restore"
        except (subprocess.SubprocessError, OSError) as exc:
            return False, f"git_revert confirmation failed — {exc}"
        return True, f"git_revert confirmed (HEAD {pre_task_head[:12]}, scope clean)"
    return False, f"unknown rollback kind {kind!r}"


@dataclass
class ExecutionResult:
    """One contract execution: status, claim, evidence, and the ledger verdict."""

    task_id: str
    status: str                      # VERIFIED | SUBMITTED | FAILED | ROLLED_BACK
    failure_kind: Optional[str]
    reason: str
    claim_id: str
    evidence_ref: str
    verdict: str
    artifact: dict
    event_ref: Optional[str]
    predicate_outcomes: list[dict] = field(default_factory=list)


def _record_for(
    contract: dict, result: dict, *, git_head: str, tenant_id: str,
) -> dict[str, Any]:
    """The producer's input — the record is the source of truth: every artifact
    field derives from it, nothing from produce-time state."""
    expected = contract.get("expected_output")
    rec = {
        "record_version": "1.0",
        "record_type": "task",
        "task_id": contract.get("task_id"),
        "contract": {
            "task_id": contract.get("task_id"),
            "objective": contract.get("objective"),
            "skill": contract.get("skill"),
            "args": contract.get("args"),
            "expected_output": expected,
            "inputs": contract.get("inputs"),
            "preconditions": contract.get("preconditions"),
            "side_effects": contract.get("side_effects"),
        },
        "outcome": result["status"],
        "failure_kind": result.get("failure_kind"),
        "exit_code": int(result.get("exit_code", 0)),
        "output": result.get("output"),
        "predicate_outcomes": result.get("predicate_outcomes") or [],
        "rollback": result.get("rollback"),
        "tenant_id": tenant_id,
        "git_head": git_head,
        "recorded_at": now_iso(),
    }
    rec["claim_id"] = task_producer._polarity_mapping(rec)[2]
    return rec


def _task_failed_event(
    contract: dict, result: ExecutionResult, *, goal: Optional[str], dag: Optional[list],
) -> dict:
    """The PINNED TASK_FAILED shape (spec §8) — the replay consumer parses
    exactly these fields, and `event.task_id == contract.task_id` verbatim so
    claim:ok:task:<task_id> collides across producers and REGRESS fires."""
    return {
        "event": "TASK_FAILED",
        "version": "1.0",
        "task_id": contract.get("task_id"),   # verbatim — never normalized
        "goal": goal or "",
        "failed_step": contract.get("task_id"),
        "failed_index": 0,
        "error": result.reason,
        "attempt": 1,
        "max_attempts": 1,
        "decision": "rollback" if result.status == "ROLLED_BACK" else "fail",
        "dag": dag or [contract],
        "revised_dag": None,
        "ts": now_iso(),
    }


def execute_contract(
    contract: dict[str, Any],
    *,
    runner: Runner,
    output_root: Path,
    ledger_dir: Path,
    git_head: str,
    tenant_id: str = "default",
    goal: Optional[str] = None,
    dag: Optional[list] = None,
    dry_run: bool = False,
    pre_task_head: Optional[str] = None,
) -> ExecutionResult:
    """Run ONE contract under its permission envelope (spec §3–§8).

    - Validates the contract first (fail fast — an invalid contract is a
      programming error; the endpoint 422s before this is ever reached).
    - Enforces the envelope on the runner's REPORTED accesses (tools, data,
      artifacts) — a task cannot access more than its contract declares.
    - Enforces the executor-side caps (budget, steps) — exceeded ⇒ FAILED.
    - exit 0 reaches SUBMITTED; only passing predicates reach VERIFIED; no
      expected_output ⇒ at most SUBMITTED.
    - FAILED + declared rollback ⇒ restore + confirm; confirmation failure is
      an incident (FAILED, never a false ROLLED_BACK).
    - Every outcome produces a §8 artifact; every FAILED/ROLLED_BACK emits the
      pinned TASK_FAILED event for the replay consumer.
    """
    if not isinstance(contract.get("task_id"), str) or not contract.get("task_id"):
        raise ValueError("legacy dag entries (no task_id) have no contract — cannot execute")
    dag_ids = {
        str(e.get("task_id"))
        for e in dag or [contract]
        if isinstance(e, dict) and isinstance(e.get("task_id"), str)
    }
    errors = validate_contract(contract, dag_ids)
    if errors:
        raise ValueError(f"invalid contract: {'; '.join(errors)}")

    task_id = str(contract.get("task_id", ""))
    constraints = contract.get("constraints") or {}
    budget_cap = float(constraints.get("budget_cap_usd", DEFAULT_BUDGET_CAP_USD))
    max_steps = int(constraints.get("max_steps", DEFAULT_MAX_STEPS))
    allowed_tools = list(contract.get("allowed_tools") or [])
    data_scope = list(contract.get("allowed_data") or [tenant_id])
    broker = ToolBroker()

    expected = contract.get("expected_output")
    preds = (expected or {}).get("predicates", []) if isinstance(expected, dict) else []
    rollback = contract.get("rollback")
    output_root = Path(output_root)

    if rollback and rollback.get("kind") == "git_revert" and pre_task_head is None:
        try:
            head = subprocess.run(
                ["git", "-C", str(output_root), "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=10,
            )
            if head.returncode == 0:
                pre_task_head = head.stdout.strip()
        except (subprocess.SubprocessError, OSError):
            pre_task_head = None

    # --- RUN (the model/broker boundary — the runner is the only hook) ---
    crash_result = ExecutionResult(
        task_id=task_id, status="FAILED", failure_kind="crash",
        reason="runner crashed: ",
        claim_id="", evidence_ref="", verdict="", artifact={}, event_ref=None,
    )
    try:
        raw = runner(contract)
    except Exception as exc:  # noqa: BLE001 — a crashing runner is a FAILED task
        crash_result.reason = f"runner crashed: {type(exc).__name__}: {exc}"
        return _finalize(
            contract, crash_result,
            {"exit_code": 1, "output": None, "steps": 1, "cost_usd": 0.0,
             "tools_used": [], "data_accessed": [], "artifacts": []},
            ledger_dir, git_head, tenant_id, goal, dag, dry_run, rollback,
        )
    runner_result: dict[str, Any] = {
        "exit_code": int(raw.get("exit_code", 1)),
        "output": raw.get("output"),
        "steps": int(raw.get("steps", 1)),
        "cost_usd": float(raw.get("cost_usd", 0.0)),
        "tools_used": list(raw.get("tools_used") or []),
        "data_accessed": list(raw.get("data_accessed") or []),
        "artifacts": list(raw.get("artifacts") or []),
    }

    # --- permission envelope enforcement ---
    def fail(kind: str, reason: str) -> ExecutionResult:
        return _finalize(
            contract,
            ExecutionResult(task_id=task_id, status="FAILED", failure_kind=kind,
                            reason=reason, claim_id="", evidence_ref="", verdict="",
                            artifact={}, event_ref=None),
            runner_result, ledger_dir, git_head, tenant_id, goal, dag, dry_run, rollback,
        )

    denied_tools = [t for t in runner_result["tools_used"] if not broker.authorize_tool(allowed_tools, t)]
    if denied_tools:
        return fail("permission", f"tool(s) outside allowed_tools: {denied_tools}")
    denied_data = [d for d in runner_result["data_accessed"] if not broker.authorize_data(data_scope, d)]
    if denied_data:
        return fail("permission", f"data outside allowed_data: {denied_data}")
    scope_violations = broker.side_effect_violations(
        list(contract.get("side_effects") or []), runner_result["artifacts"], output_root,
    )
    if scope_violations:
        return fail("scope", "; ".join(scope_violations))

    # --- executor-side caps ---
    if runner_result["steps"] > max_steps:
        return fail("steps", f"steps {runner_result['steps']} > max_steps {max_steps}")
    if runner_result["cost_usd"] > budget_cap:
        return fail("budget", f"cost ${runner_result['cost_usd']:.4f} > budget ${budget_cap:.4f}")

    # --- SUBMITTED / VERIFIED (exit 0 is NOT done) ---
    if runner_result["exit_code"] != 0:
        return fail("crash", f"exit_code {runner_result['exit_code']} != 0")

    if expected is None:
        # no expected_output ⇒ at most SUBMITTED — the claim can never VERIFIED
        return _finalize(
            contract,
            ExecutionResult(task_id=task_id, status="SUBMITTED", failure_kind=None,
                            reason="executed, exit 0, no expected_output — never VERIFIED",
                            claim_id="", evidence_ref="", verdict="", artifact={}, event_ref=None),
            runner_result, ledger_dir, git_head, tenant_id, goal, dag, dry_run, rollback,
        )

    outcomes = run_predicates(preds, output_root, {
        "exit_code": runner_result["exit_code"], "output": runner_result["output"],
    })
    if predicates_pass(outcomes):
        status, kind, reason = "VERIFIED", None, "all expected_output predicates pass"
    else:
        failed = [o for o in outcomes if not o.get("passed")]
        status, kind, reason = "FAILED", "predicates", (
            f"{len(failed)}/{len(outcomes)} predicates fail: "
            + "; ".join(f"{o.get('kind')}: {o.get('detail')}" for o in failed[:3])
        )
    result = ExecutionResult(
        task_id=task_id, status=status, failure_kind=kind, reason=reason,
        claim_id="", evidence_ref="", verdict="", artifact={}, event_ref=None,
        predicate_outcomes=outcomes,
    )

    # --- rollback on FAILED (restore + confirm; confirmation failure is an incident) ---
    if result.status == "FAILED" and rollback:
        confirmed, detail = _run_rollback(rollback, runner_result["artifacts"], output_root, pre_task_head)
        if confirmed:
            result.status = "ROLLED_BACK"
            result.reason = f"{result.reason} — rollback {detail}"
        else:
            result.failure_kind = "rollback_failed"
            result.reason = f"{result.reason} — {detail}"

    return _finalize(
        contract, result, runner_result, ledger_dir, git_head, tenant_id,
        goal, dag, dry_run, rollback,
    )


def _finalize(
    contract: dict,
    result: ExecutionResult,
    runner_result: dict,
    ledger_dir: Path,
    git_head: str,
    tenant_id: str,
    goal: Optional[str],
    dag: Optional[list],
    dry_run: bool,
    rollback: Optional[dict],
) -> ExecutionResult:
    """Emit the §8 artifact (+ TASK_FAILED event when failed) and return the
    filled result. Every outcome lands in the ledger; dry_run computes only."""
    rec = _record_for(contract, {
        "status": result.status, "failure_kind": result.failure_kind,
        "exit_code": runner_result.get("exit_code", 0), "output": runner_result.get("output"),
        "predicate_outcomes": result.predicate_outcomes, "rollback": rollback,
    }, git_head=git_head, tenant_id=tenant_id)
    out = task_producer.produce(rec, ledger_dir, git_head, dry_run=dry_run)
    result.claim_id = out["claim_id"]
    result.evidence_ref = out["evidence_ref"]
    result.verdict = out["verdict"]
    result.artifact = out["artifact"]
    if result.status in ("FAILED", "ROLLED_BACK") and not dry_run:
        event = _task_failed_event(contract, result, goal=goal, dag=dag)
        stream = task_producer.append_task_event(event, ledger_dir)
        result.event_ref = str(stream)
    return result


def advance_dag(
    dag: list[dict],
    *,
    runner: Runner,
    output_root: Path,
    ledger_dir: Path,
    git_head: str,
    tenant_id: str = "default",
    goal: Optional[str] = None,
    dry_run: bool = False,
) -> tuple[list[dict], Optional[ExecutionResult]]:
    """Spec §9 pinned granularity: select exactly ONE READY task whose
    preconditions hold, execute it under its contract, return the dag with the
    executed task's status advanced. Returns (dag, None) when nothing is
    ready. The next call with the same dag advances the graph one more node."""
    entry = select_ready_task(dag)
    if entry is None:
        return dag, None
    dag_ids = {
        str(e.get("task_id"))
        for e in dag
        if isinstance(e, dict) and isinstance(e.get("task_id"), str)
    }
    errors = validate_contract(entry, dag_ids)
    if errors:
        raise ValueError(f"invalid contract: {'; '.join(errors)}")
    result = execute_contract(
        entry, runner=runner, output_root=output_root, ledger_dir=ledger_dir,
        git_head=git_head, tenant_id=tenant_id, goal=goal, dag=dag, dry_run=dry_run,
    )
    updated = [dict(e) for e in dag]
    for e in updated:
        if isinstance(e, dict) and e.get("task_id") == entry.get("task_id"):
            e["status"] = result.status
    return updated, result


class StubRunner:
    """Deterministic zero-spend runner (CI/test profile, spec §10): executes
    nothing real. It writes fixture files per `args.writes` under the output
    root and reports the accesses/cost from `args`; the contract's PREDICATES
    then run for real against what it wrote. exit 0 never implies done — the
    contract decides."""
    def __init__(self, output_root: Path):
        self.output_root = Path(output_root)
        self.calls = 0

    def __call__(self, contract: dict) -> dict:
        self.calls += 1
        args = contract.get("args") or {}
        for spec in args.get("writes") or []:
            p = self.output_root / spec["path"]
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(str(spec.get("content", "")), encoding="utf-8")
        return {
            "exit_code": int(args.get("exit_code", 0)),
            "output": args.get("output"),
            "steps": int(args.get("steps", 1)),
            "cost_usd": float(args.get("cost_usd", 0.0)),
            "tools_used": list(args.get("tools_used") or []),
            "data_accessed": list(args.get("data_accessed") or []),
            "artifacts": list(args.get("artifacts") or []),
        }


def run_self_test() -> int:
    """Zero-spend verification of the executor (spec §10): predicate kinds,
    cap enforcement, permission denial, rollback confirmation, polarity
    mapping, prohibited transitions — all with the StubRunner."""
    import tempfile

    def contract(task_id="t.1", **over):
        base = {
            "task_id": task_id, "objective": f"objective {task_id}", "skill": "stub",
            "args": {}, "status": "READY",
        }
        base.update(over)
        return base

    def run_one(ledger, output, c, runner=None, **kw):
        r = runner or StubRunner(output)
        return execute_contract(c, runner=r, output_root=output, ledger_dir=ledger,
                                git_head="deadbeef", **kw)

    with tempfile.TemporaryDirectory(prefix="task-executor-self-test-") as tmp:
        root = Path(tmp)
        ledger = root / "ledger"

        # 1. VERIFIED via real predicates (exit 0 + file_exists + file_contains)
        c1 = contract(
            args={"writes": [{"path": "out/retrieval.py", "content": "def search(): return 1"}]},
            side_effects=[{"kind": "file_write", "path": "out/**"}],
            allowed_tools=["filesystem"],
            expected_output={"schema": {}, "predicates": [
                {"kind": "file_exists", "path": "out/retrieval.py"},
                {"kind": "file_contains", "path": "out/retrieval.py", "text": "def search"},
            ]},
        )
        r1 = run_one(ledger, root / "out1", c1)
        assert r1.status == "VERIFIED", r1.reason
        assert r1.artifact["polarity"] == "SUPPORTING"
        assert r1.claim_id.startswith("claim:done:task:")

        # 2. exit 0 ≠ VERIFIED — exit 0 but predicates fail (file never written)
        c2 = contract(task_id="t.2", args={"exit_code": 0},
                      expected_output={"schema": {}, "predicates": [
                          {"kind": "file_exists", "path": "out/never.py"},
                      ]})
        r2 = run_one(ledger, root / "out2", c2)
        assert r2.status == "FAILED" and r2.failure_kind == "predicates", r2.reason
        assert r2.artifact["evidence_type"] == "task_failed_verify"
        assert r2.event_ref, "TASK_FAILED event must be emitted"
        assert r2.artifact["polarity"] == "CONTRADICTING"

        # 3. permission denial — tool outside allowed_tools
        c3 = contract(task_id="t.3", allowed_tools=["filesystem"],
                      args={"tools_used": ["network"]})
        r3 = run_one(ledger, root / "out3", c3)
        assert r3.status == "FAILED" and r3.failure_kind == "permission"
        assert "network" in r3.reason
        assert r3.claim_id == task_producer.claim_ok_task("t.3")

        # 4. scope violation — artifact outside declared side effects
        c4 = contract(task_id="t.4", allowed_tools=["filesystem"],
                      side_effects=[{"kind": "file_write", "path": "out/**"}],
                      args={"artifacts": ["/etc/passwd"]})
        r4 = run_one(ledger, root / "out4", c4)
        assert r4.status == "FAILED" and r4.failure_kind == "scope"

        # 5. no expected_output ⇒ SUBMITTED, INCONCLUSIVE, never VERIFIED
        c5 = contract(task_id="t.5", args={"exit_code": 0})
        r5 = run_one(ledger, root / "out5", c5)
        assert r5.status == "SUBMITTED"
        assert r5.artifact["polarity"] == "INCONCLUSIVE"
        assert r5.artifact["evidence_type"] == "task_submitted"
        assert r5.verdict == "UNVERIFIED"

        # 6. budget cap — cost over the cap ⇒ FAILED budget
        c6 = contract(task_id="t.6", constraints={"budget_cap_usd": 0.01},
                      args={"cost_usd": 0.5})
        r6 = run_one(ledger, root / "out6", c6)
        assert r6.status == "FAILED" and r6.failure_kind == "budget"

        # 7. max_steps cap
        c7 = contract(task_id="t.7", constraints={"max_steps": 3}, args={"steps": 9})
        r7 = run_one(ledger, root / "out7", c7)
        assert r7.status == "FAILED" and r7.failure_kind == "steps"

        # 8. rollback file_delete: FAILED + rollback ⇒ ROLLED_BACK, files confirmed gone
        c8 = contract(
            task_id="t.8", args={"exit_code": 0,
                                 "writes": [{"path": "out/junk.txt", "content": "x"}],
                                 "artifacts": ["out/junk.txt"]},
            side_effects=[{"kind": "file_write", "path": "out/**"}],
            rollback={"kind": "file_delete", "scope": "out/"},
            expected_output={"schema": {}, "predicates": [
                {"kind": "file_exists", "path": "out/real.txt"},
            ]},
        )
        r8 = run_one(ledger, root / "out8", c8)
        assert r8.status == "ROLLED_BACK", r8.reason
        assert not (root / "out8" / "out" / "junk.txt").exists(), "file_delete must confirm removal"
        assert r8.artifact["polarity"] == "CONTRADICTING"
        assert r8.claim_id.startswith("claim:ok:task:")

        # 9. rollback confirmation failure = incident (git_revert with no repo)
        c9 = contract(task_id="t.9", args={"exit_code": 0},
                      rollback={"kind": "git_revert", "scope": "out/"},
                      expected_output={"schema": {}, "predicates": [
                          {"kind": "file_exists", "path": "out/x.txt"},
                      ]})
        r9 = run_one(ledger, root / "out9", c9)
        assert r9.status == "FAILED" and r9.failure_kind == "rollback_failed", r9.reason

        # 10. select_ready_task respects preconditions
        dag = [
            contract(task_id="a", status="VERIFIED"),
            contract(task_id="b", preconditions=["a:VERIFIED"]),
            contract(task_id="c", status="READY", preconditions=["b:VERIFIED"]),
        ]
        picked = select_ready_task(dag)
        assert picked is not None and picked["task_id"] == "b"

        # 11. advance_dag advances exactly one node per call
        dag2 = [
            contract(task_id="x", status="READY", args={"exit_code": 0, "output": "done"},
                     expected_output={"schema": {}, "predicates": [
                         {"kind": "exit_code", "code": 0},
                         {"kind": "output_equals", "expected": "done"},
                     ]}),
            contract(task_id="y", status="READY", preconditions=["x:VERIFIED"]),
        ]
        dag2a, res_a = advance_dag(dag2, runner=StubRunner(root / "out11"), output_root=root / "out11",
                                   ledger_dir=ledger, git_head="deadbeef")
        assert res_a is not None and res_a.status == "VERIFIED"
        assert {e.get("task_id"): e.get("status") for e in dag2a} == {"x": "VERIFIED", "y": "READY"}
        dag2b, res_b = advance_dag(dag2a, runner=StubRunner(root / "out11"), output_root=root / "out11",
                                   ledger_dir=ledger, git_head="deadbeef")
        assert res_b is not None and res_b.status == "SUBMITTED"  # no expected_output on y

        # 12. idempotent re-execution of the SAME contract ingests 0 new
        registry_before = len(task_producer.load_registry(ledger / "claims.json")["claims"])
        run_one(ledger, root / "out1", c1)  # same contract, same args → same record
        registry_after = len(task_producer.load_registry(ledger / "claims.json")["claims"])
        assert registry_after == registry_before, "re-execution must not add claims"

        return 0
