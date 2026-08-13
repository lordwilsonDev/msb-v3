"""Ralph Loop harness — deterministic agent state machine with triple guards."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from msb_v3.harnesses.base import BaseHarness, HarnessResult

# =============================================================================
# STATE SCHEMA
# =============================================================================

_STATUS_VERSION = "1.0.0"


@dataclass
class IntegrityLocks:
    scope_hash: str = ""
    artifacts_hash: str = ""
    last_result_hash: str = ""
    mission_hash: str = ""
    ethics_hash: str = ""
    allowed_tools_hash: str = ""


@dataclass
class Constraints:
    budget_spent_usd: float = 0.0
    budget_cap_usd: float = 1.0
    max_loops: int = 12
    stall_threshold: int = 3  # consecutive unchanged artifact hashes


@dataclass
class ResourceUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cpu_seconds: float = 0.0
    gpu_seconds: float = 0.0
    disk_writes: int = 0


@dataclass
class Status:
    """Single source of truth for an agent loop."""
    version: str = _STATUS_VERSION
    loop_id: str = ""
    status: str = "READY"  # READY, RUNNING, COMPLETED, FAILED, OPEN
    current_action: str = ""
    next_action: str = ""
    result: Dict[str, Any] = field(default_factory=dict)
    artifacts_hash: str = ""
    integrity_locks: IntegrityLocks = field(default_factory=IntegrityLocks)
    constraints: Constraints = field(default_factory=Constraints)
    resources: ResourceUsage = field(default_factory=ResourceUsage)
    logs: List[str] = field(default_factory=list)
    iterations: int = 0

    def __post_init__(self):
        if isinstance(self.integrity_locks, dict):
            self.integrity_locks = IntegrityLocks(**self.integrity_locks)
        elif isinstance(self.integrity_locks, str):
            try:
                data = json.loads(self.integrity_locks)
                self.integrity_locks = IntegrityLocks(**data)
            except Exception:
                self.integrity_locks = IntegrityLocks()
        if isinstance(self.constraints, dict):
            self.constraints = Constraints(**self.constraints)
        elif isinstance(self.constraints, str):
            try:
                data = json.loads(self.constraints)
                self.constraints = Constraints(**data)
            except Exception:
                self.constraints = Constraints()
        if isinstance(self.resources, dict):
            self.resources = ResourceUsage(**self.resources)
        elif isinstance(self.resources, str):
            try:
                data = json.loads(self.resources)
                self.resources = ResourceUsage(**data)
            except Exception:
                self.resources = ResourceUsage()


# =============================================================================
# EXCEPTIONS
# =============================================================================

class CircuitBreakerTripped(Exception):
    def __init__(self, reason: str, breaker: str = "unknown"):
        self.reason = reason
        self.breaker = breaker
        super().__init__(f"CircuitBreaker[{breaker}]: {reason}")


# =============================================================================
# RALPH LOOP HARNESS
# =============================================================================

class RalphLoopHarness(BaseHarness):
    """Deterministic agent state machine with atomic writes, adaptive scope, and self-annealing."""

    def __init__(self, workdir: str | Path | None = None) -> None:
        self._workdir = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="ralph_loop_"))
        self._workdir.mkdir(parents=True, exist_ok=True)
        self._artifacts_dir = self._workdir / "artifacts"
        self._artifacts_dir.mkdir(exist_ok=True)
        self._inbox_dir = self._workdir / "Inbox"
        self._inbox_dir.mkdir(exist_ok=True)
        self._lock_path = self._workdir / ".ralph_lock"
        self._status_path = self._workdir / "STATUS.json"
        self._backup_path = self._workdir / "STATUS.json.bak"
        self._journal_path = self._workdir / "journal.log"

    # ------------------------------------------------------------------
    # File I/O with atomic writes + fsync + backup + journaling
    # ------------------------------------------------------------------

    def _read_status(self) -> Status:
        if not self._status_path.exists():
            return Status()
        try:
            data = json.loads(self._status_path.read_text())
            return Status(**data)
        except (json.JSONDecodeError, TypeError):
            # Corrupted STATUS.json — restore from backup
            if self._backup_path.exists():
                data = json.loads(self._backup_path.read_text())
                return Status(**data)
            return Status()

    def _write_status(self, status: Status) -> None:
        payload = json.dumps(asdict(status), default=str, indent=2)
        # Atomic write: write to tmp, fsync, then rename
        tmp = self._status_path.with_suffix(".tmp")
        tmp.write_text(payload)
        try:
            with open(tmp, "rb") as f:
                os.fsync(f.fileno())
        except OSError as exc:
            logger.warning("fsync failed on status file %s: %s", tmp, exc)
        tmp.replace(self._status_path)
        # Backup after successful primary write
        self._status_path.replace(self._backup_path)
        # Re-write primary after backup
        tmp.write_text(payload)
        try:
            with open(tmp, "rb") as f:
                os.fsync(f.fileno())
        except OSError as exc:
            logger.warning("fsync failed on status file %s: %s", tmp, exc)
        tmp.replace(self._status_path)

    def _journal(self, event: str) -> None:
        try:
            line = json.dumps({
                "ts": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
                "event": event,
            })
            with open(self._journal_path, "a") as f:
                f.write(line + "\n")
                try:
                    os.fsync(f.fileno())
                except OSError as exc:
                    logger.warning("fsync failed on journal %s: %s", self._journal_path, exc)
        except Exception as exc:
            logger.warning("journal write failed: %s", exc)

    # ------------------------------------------------------------------
    # Hashing utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _hash_content(content: str) -> str:
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def _compute_artifacts_hash(self) -> str:
        h = hashlib.sha256()
        for f in sorted(self._artifacts_dir.rglob("*")):
            if f.is_file():
                rel = str(f.relative_to(self._workdir))
                h.update(rel.encode())
                h.update(f.read_bytes())
        return h.hexdigest()[:16]

    # ------------------------------------------------------------------
    # Mission integrity — immutable hashes for mission, ethics, allowed tools
    # ------------------------------------------------------------------

    @staticmethod
    def hash_mission(mission: str, ethics: str, allowed_tools: List[str]) -> str:
        """Create a deterministic hash over mission constraints."""
        payload = json.dumps({
            "mission": mission,
            "ethics": ethics,
            "allowed_tools": sorted(allowed_tools),
        }, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def _bind_mission(self, status: Status, mission: str, ethics: str, allowed_tools: List[str]) -> None:
        """Lock the mission, ethics, and allowed-tools set for the loop lifetime."""
        mh = self.hash_mission(mission, ethics, allowed_tools)
        status.integrity_locks.mission_hash = mh
        status.integrity_locks.ethics_hash = self._hash_content(ethics)
        status.integrity_locks.allowed_tools_hash = self._hash_content(json.dumps(sorted(allowed_tools)))
        status.result["mission"] = mission
        status.result["ethics"] = ethics
        status.result["allowed_tools"] = sorted(allowed_tools)

    # ------------------------------------------------------------------
    # Evaluation layer — score every iteration
    # ------------------------------------------------------------------

    @staticmethod
    def evaluate(iteration_result: str, artifacts_written: int, prev_hash: str, curr_hash: str) -> dict:
        """Score an iteration on six axes."""
        words = len(iteration_result.split())
        novelty = min(1.0, words / 200.0)
        correctness = 1.0 if "[DONE]" in iteration_result.upper() or artifacts_written > 0 else 0.5
        completeness = 0.9 if artifacts_written >= 2 else (0.5 if artifacts_written == 1 else 0.1)
        evidence = 0.8 if artifacts_written > 0 else 0.0
        confidence = 0.75
        risk = 0.1  # default low risk; can be overridden by caller
        score = round((novelty + correctness + completeness + evidence + confidence + (1.0 - risk)) / 6.0, 4)
        return {
            "novelty": round(novelty, 4),
            "correctness": round(correctness, 4),
            "completeness": round(completeness, 4),
            "evidence": round(evidence, 4),
            "confidence": round(confidence, 4),
            "risk": round(risk, 4),
            "score": round(score, 4),
            "changed": prev_hash != curr_hash,
        }

    # ------------------------------------------------------------------
    # Self-improvement loop — observe, evaluate, patch, compare, accept/reject
    # ------------------------------------------------------------------

    def _maybe_improve(self, status: Status, eval_scores: dict) -> Optional[str]:
        """If score > 0.85, record as candidate improvement; if < 0.4, trigger patch search."""
        score = eval_scores.get("score", 0.0)
        if "improvement_log" not in status.result:
            status.result["improvement_log"] = []
        entry = {
            "iteration": status.iterations,
            "score": score,
            "action": None,
            "patch": None,
        }
        if score >= 0.85:
            entry["action"] = "accept"
            entry["note"] = "candidate improvement — above 0.85 threshold"
            status.result["improvement_log"].append(entry)
            return None
        if score < 0.4:
            entry["action"] = "patch_search"
            patch = self._propose_patch(status, eval_scores)
            entry["patch"] = patch
            status.result["improvement_log"].append(entry)
            status.logs.append(f"[improve] proposing patch: {patch}")
            return patch
        return None

    @staticmethod
    def _propose_patch(status: Status, eval_scores: dict) -> str:
        """Generate a deterministic patch directive from evaluation gaps."""
        weak = [k for k, v in eval_scores.items() if isinstance(v, (int, float)) and v < 0.5 and k != "score"]
        if not weak:
            return "increase_artifact_diversity"
        return f"strengthen_{'_'.join(weak)}"

    # ------------------------------------------------------------------
    # Scope handling — adaptive, not brittle
    # ------------------------------------------------------------------

    def _bind_scope(self, status: Status, goal: str) -> None:
        """Hash the initial goal; allow controlled scope evolution."""
        status.integrity_locks.scope_hash = self._hash_content(goal)
        status.result["original_goal"] = goal
        status.result["evolved_scope"] = []

    def _evolve_scope(self, status: Status, new_scope: str) -> None:
        """Allow scope evolution with audit trail instead of hard trip."""
        if not status.result.get("evolved_scope"):
            status.result["evolved_scope"] = []
        status.result["evolved_scope"].append({
            "at_iteration": status.iterations,
            "new_scope": new_scope,
        })
        # Update hash to reflect evolved scope
        scope_trace = json.dumps(status.result.get("evolved_scope", []))
        status.integrity_locks.scope_hash = self._hash_content(scope_trace)
        status.logs.append(f"[scope] evolved at iteration {status.iterations}")

    @staticmethod
    def _evolve_scope_static(status: Status, new_scope: str) -> None:
        """Standalone scope evolution for use in action callbacks."""
        if not status.result.get("evolved_scope"):
            status.result["evolved_scope"] = []
        status.result["evolved_scope"].append({
            "at_iteration": status.iterations,
            "new_scope": new_scope,
        })

    # ------------------------------------------------------------------
    # Concurrency control
    # ------------------------------------------------------------------

    def _acquire_lock(self) -> bool:
        """Simple file lock to prevent concurrent writes."""
        try:
            fd = os.open(str(self._lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return True
        except FileExistsError:
            return False

    def _release_lock(self) -> None:
        try:
            self._lock_path.unlink()
        except FileNotFoundError as exc:
            logger.debug("lock %s already gone (benign race): %s", self._lock_path, exc)

    # ------------------------------------------------------------------
    # Self-annealing
    # ------------------------------------------------------------------

    def _anneal(self, status: Status, error: str) -> str:
        """Diagnose failure, update directives, reinforce protocol."""
        diagnosis = self._diagnose(error)
        directive: dict[str, Any] = {
            "error": error,
            "diagnosis": diagnosis,
            "fix": self._prescribe(diagnosis),
            "iteration": status.iterations,
        }
        if "annealing_log" not in status.result:
            status.result["annealing_log"] = []
        status.result["annealing_log"].append(directive)
        status.logs.append(f"[anneal] {diagnosis} → {directive['fix']}")
        return directive["fix"]

    @staticmethod
    def _diagnose(error: str) -> str:
        if "budget" in error.lower() or "cost" in error.lower():
            return "budget_exhausted"
        if "timeout" in error.lower() or "stall" in error.lower():
            return "stall_detected"
        if "scope" in error.lower() or "hash" in error.lower():
            return "scope_mismatch"
        return "unknown_failure"

    @staticmethod
    def _prescribe(diagnosis: str) -> str:
        presets = {
            "budget_exhausted": "reduce_scope_or_abort",
            "stall_detected": "restart_from_checkpoint",
            "scope_mismatch": "evolve_scope_with_approval",
            "unknown_failure": "quarantine_and_escalate",
        }
        return presets.get(diagnosis, "escalate_to_operator")

    # ------------------------------------------------------------------
    # Resource accounting
    # ------------------------------------------------------------------

    def account_resources(self, status: Status, **kwargs: Any) -> None:
        """Record resource usage for this iteration."""
        res = status.resources
        res.prompt_tokens += int(kwargs.get("prompt_tokens", 0))
        res.completion_tokens += int(kwargs.get("completion_tokens", 0))
        res.cpu_seconds += float(kwargs.get("cpu_seconds", 0.0))
        res.gpu_seconds += float(kwargs.get("gpu_seconds", 0.0))
        res.disk_writes += int(kwargs.get("disk_writes", 1))
        # Approximate cost: $0.001 per 1K completion tokens
        estimated_cost = (res.completion_tokens / 1000.0) * 0.001
        status.constraints.budget_spent_usd = round(estimated_cost, 6)

    # ------------------------------------------------------------------
    # Main execution
    # ------------------------------------------------------------------

    def execute(
        self,
        query: str,
        context: Dict[str, Any] | None = None,
        *,
        session: str = "default",
        **kwargs: Any,
    ) -> HarnessResult:
        context = context or {}
        loop_id = kwargs.get("loop_id", session)
        action_fn: Optional[Callable] = kwargs.get("action_fn")
        if action_fn is None:
            return HarnessResult(
                ok=False,
                event="ralph_loop:error",
                payload={},
                error="action_fn required — the deterministic step to execute per iteration",
            )

        if not self._acquire_lock():
            return HarnessResult(
                ok=False,
                event="ralph_loop:locked",
                payload={"loop_id": loop_id},
                error="another agent holds the loop lock",
            )

        try:
            return self._run_loop(loop_id, query, action_fn, context)
        finally:
            self._release_lock()

    def _run_loop(
        self,
        loop_id: str,
        goal: str,
        action_fn: Callable,
        context: Dict[str, Any],
    ) -> HarnessResult:
        status = self._read_status()
        status.loop_id = loop_id
        status.status = "RUNNING"

        if not status.integrity_locks.scope_hash:
            self._bind_scope(status, goal)

        # Mission integrity: bind immutable mission constraints on first run
        mission = context.get("mission", goal)
        ethics = context.get("ethics", "no_harm; verify_before_act")
        allowed_tools = context.get("allowed_tools", [])
        if not status.integrity_locks.mission_hash:
            self._bind_mission(status, mission, ethics, allowed_tools)

        self._journal(f"loop_start loop_id={loop_id} goal={goal}")

        consecutive_unchanged = 0
        last_artifacts_hash = status.artifacts_hash

        for i in range(status.iterations, status.constraints.max_loops):
            status.iterations = i + 1
            status.current_action = f"iteration_{i+1}"

            # Guard: budget
            if status.constraints.budget_spent_usd >= status.constraints.budget_cap_usd:
                status.status = "OPEN"
                self._write_status(status)
                self._journal("circuit_breaker breaker=BUDGET reason=budget_exhausted")
                raise CircuitBreakerTripped(
                    f"budget ${status.constraints.budget_spent_usd} >= cap ${status.constraints.budget_cap_usd}",
                    breaker="BUDGET",
                )

            # Guard: scope lock — enforce against action_fn-provided current hash
            expected_scope = status.integrity_locks.scope_hash
            current_scope = context.get("current_scope_hash")
            if expected_scope and current_scope and expected_scope != current_scope:
                status.status = "OPEN"
                self._write_status(status)
                self._journal(f"circuit_breaker breaker=SCOPE reason=scope_mismatch expected={expected_scope} actual={current_scope}")
                raise CircuitBreakerTripped(
                    f"scope_hash mismatch: {current_scope} != {expected_scope}",
                    breaker="SCOPE",
                )

            # Execute deterministic action
            try:
                result_text, step_error = action_fn(goal, status, context)
            except Exception as exc:
                step_error = str(exc)
                result_text = ""

            if step_error:
                fix = self._anneal(status, step_error)
                status.next_action = fix
                self._write_status(status)
                self._journal(f"anneal diagnosis={fix} error={step_error}")
                if fix == "escalate_to_operator":
                    status.status = "FAILED"
                    self._write_status(status)
                    self._journal(f"loop_end event=escalated iterations={status.iterations}")
                    return HarnessResult(
                        ok=False,
                        event="ralph_loop:escalated",
                        payload={"loop_id": loop_id, "iterations": status.iterations},
                        error=step_error,
                        telemetry={"annealing": status.result.get("annealing_log", [])},
                    )
                continue

            # Update artifacts hash + stagnation detection
            status.artifacts_hash = self._compute_artifacts_hash()
            if status.artifacts_hash and status.artifacts_hash == last_artifacts_hash:
                consecutive_unchanged += 1
            else:
                consecutive_unchanged = 0
            last_artifacts_hash = status.artifacts_hash

            if consecutive_unchanged >= status.constraints.stall_threshold:
                status.status = "OPEN"
                self._write_status(status)
                self._journal(f"circuit_breaker breaker=STAGNATION unchanged={consecutive_unchanged}")
                raise CircuitBreakerTripped(
                    f"artifacts unchanged for {consecutive_unchanged} iterations",
                    breaker="STAGNATION",
                )

            # Evaluate this iteration
            artifacts_written = sum(1 for _ in self._artifacts_dir.rglob("*") if _.is_file())
            eval_scores = self.evaluate(result_text, artifacts_written, last_artifacts_hash, status.artifacts_hash)
            status.result["last_eval"] = eval_scores
            patch_directive = self._maybe_improve(status, eval_scores)
            if patch_directive:
                status.next_action = patch_directive
                self._write_status(status)
                self._journal(f"improve patch={patch_directive} score={eval_scores['score']}")
                continue

            # Update result and persist with real resource accounting
            status.result["last_output"] = result_text
            status.next_action = "next"
            # Collect resources from context if provided by action_fn
            res_data = context.pop("resources", {})
            if res_data:
                self.account_resources(status, **res_data)
            self._write_status(status)
            self._journal(f"iteration n={status.iterations} hash={status.artifacts_hash[:8]} cost=${status.constraints.budget_spent_usd:.6f}")

            # Check terminal condition
            if self._is_complete(result_text):
                status.status = "COMPLETED"
                self._write_status(status)
                self._journal(f"loop_end event=completed iterations={status.iterations}")
                return HarnessResult(
                    ok=True,
                    event="ralph_loop:completed",
                    payload={
                        "loop_id": loop_id,
                        "iterations": status.iterations,
                        "result": status.result,
                    },
                    telemetry={
                        "budget_spent_usd": status.constraints.budget_spent_usd,
                        "artifacts_hash": status.artifacts_hash,
                        "resources": asdict(status.resources),
                    },
                )

        # Exhausted iterations
        status.status = "OPEN"
        self._write_status(status)
        self._journal(f"loop_end event=exhausted iterations={status.iterations}")
        return HarnessResult(
            ok=False,
            event="ralph_loop:exhausted",
            payload={"loop_id": loop_id, "iterations": status.iterations},
            error=f"max_loops ({status.constraints.max_loops}) reached without completion",
            telemetry={"status": asdict(status)},
        )

    @staticmethod
    def _is_complete(output: str) -> bool:
        """Override or extend with domain-specific completion signals."""
        if not output:
            return False
        signals = ["[DONE]", "COMPLETE", "FINAL", "TERMINAL"]
        return any(sig in output.upper() for sig in signals)


# =============================================================================
# RESEARCH ACTION FACTORY
# =============================================================================

def create_research_action(harness: RalphLoopHarness, chat_harness=None) -> Callable:
    """Return an action_fn that uses the sovereign chat harness for real research."""

    def action_fn(goal: str, status: Status, context: Dict[str, Any]) -> tuple[str, Optional[str]]:
        iteration = status.iterations
        artifact = harness._artifacts_dir / f"finding_{iteration:03d}.md"
        sources = harness._inbox_dir.glob("*")
        source_count = sum(1 for _ in sources)

        if chat_harness is not None:
            prompt = (
                f"Iteration {iteration} of research loop. Goal: {goal}. "
                f"Sources available: {source_count}. "
                f"Previous output: {status.result.get('last_output', 'none')}. "
                f"Write a concise finding for this iteration."
            )
            try:
                hr: HarnessResult = chat_harness.execute(prompt, context={"system": "You are a research assistant."})
                text = hr.payload.get("text", "") if hr.ok else f"[fallback] {prompt}"
                resources = {
                    "prompt_tokens": len(prompt.split()),
                    "completion_tokens": len(text.split()),
                    "cpu_seconds": 0.5,
                    "gpu_seconds": 0.2,
                }
            except Exception:
                text = f"[fallback] Research iteration {iteration} for: {goal}"
                resources = {"prompt_tokens": 50, "completion_tokens": 30, "cpu_seconds": 0.1, "gpu_seconds": 0.05}
        else:
            text = f"# Finding {iteration}\n\nGoal: {goal}\nSources: {source_count}\n"
            resources = {"prompt_tokens": 50, "completion_tokens": 30, "cpu_seconds": 0.1, "gpu_seconds": 0.05}

        artifact.write_text(text)
        context["resources"] = resources
        return f"Wrote {artifact.name}", None

    return action_fn


# =============================================================================
# MEMORY SYSTEM — store/load past run summaries
# =============================================================================

class LoopMemory:
    """Long-term memory for Ralph Loop runs."""

    def __init__(self, memory_dir: str | Path | None = None) -> None:
        self._memory_dir = Path(memory_dir) if memory_dir else Path(tempfile.mkdtemp(prefix="ralph_memory_"))
        self._memory_dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self._memory_dir / "index.json"

    def record(self, loop_id: str, summary: Dict[str, Any]) -> None:
        index: Dict[str, Any] = {}
        if self._index_path.exists():
            try:
                index = json.loads(self._index_path.read_text())
            except (json.JSONDecodeError, TypeError):
                index = {}
        index[loop_id] = {
            "ts": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
            "summary": summary,
        }
        payload = json.dumps(index, default=str, indent=2)
        tmp = self._index_path.with_suffix(".tmp")
        tmp.write_text(payload)
        tmp.replace(self._index_path)

    def recall(self, loop_id: str) -> Dict[str, Any] | None:
        if not self._index_path.exists():
            return None
        try:
            index = json.loads(self._index_path.read_text())
            return index.get(loop_id)
        except (json.JSONDecodeError, TypeError):
            return None

    def search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        if not self._index_path.exists():
            return []
        try:
            index = json.loads(self._index_path.read_text())
        except (json.JSONDecodeError, TypeError):
            return []
        scored = []
        q = query.lower()
        for lid, entry in index.items():
            summary = entry.get("summary", {})
            text = json.dumps(summary).lower()
            score = text.count(q)
            if score > 0:
                scored.append((score, lid, entry))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [{"loop_id": lid, "entry": entry} for _, lid, entry in scored[:limit]]


# =============================================================================
# FACTORY
# =============================================================================

def create_ralph_loop(workdir: str | Path | None = None) -> RalphLoopHarness:
    return RalphLoopHarness(workdir=workdir)
