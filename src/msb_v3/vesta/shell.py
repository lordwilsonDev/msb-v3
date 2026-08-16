"""Approval-only, non-shell command execution for the Vesta perimeter."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import sqlite3
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from msb_v3.core.config import settings
from msb_v3.governance.killswitch import KillSwitch
from msb_v3.uac.audit_chain import AuditChainLike, verify_trustworthy
from msb_v3.vesta.evidence import EvidenceError, EvidenceStore
from msb_v3.vesta.models import ABind, VestaShellRequest
from msb_v3.vesta.policy import authorize_shell
from msb_v3.vesta.runtime import TaskLifecycleError, VestaTaskStore


class ShellCapabilityError(ValueError):
    """Raised when a command violates the shell capability contract."""


@dataclass(frozen=True)
class ShellExecutionResult:
    executable: str
    args: tuple[str, ...]
    returncode: Optional[int]
    stdout: str
    stderr: str
    timed_out: bool
    output_truncated: bool
    duration_ms: int

    def as_dict(self) -> Dict[str, Any]:
        return {
            "executable": self.executable,
            "args": list(self.args),
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "timed_out": self.timed_out,
            "output_truncated": self.output_truncated,
            "duration_ms": self.duration_ms,
        }


class ShellExecutor:
    """Run only named, absolute executables without invoking a shell."""

    DEFAULT_COMMANDS = {"echo": "/bin/echo", "pwd": "/bin/pwd"}

    def __init__(
        self,
        root: str | Path,
        *,
        allowed_commands: Optional[Mapping[str, str]] = None,
        timeout_s: float = 10.0,
        max_output_bytes: int = 65_536,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.timeout_s = timeout_s
        self.max_output_bytes = max_output_bytes
        configured = dict(allowed_commands or self.DEFAULT_COMMANDS)
        self.allowed_commands: Dict[str, str] = {}
        for name, executable in configured.items():
            if not name or "/" in name or "\\" in name:
                continue
            path = Path(executable).expanduser()
            if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
                continue
            self.allowed_commands[name] = str(path.resolve())

    def validate_command(self, executable: str, args: List[str]) -> None:
        if executable not in self.allowed_commands:
            raise ShellCapabilityError("executable is not allowlisted")
        if len(args) > 32:
            raise ShellCapabilityError("too many command arguments")
        total = 0
        for arg in args:
            if "\x00" in arg or len(arg) > 1024:
                raise ShellCapabilityError("invalid command argument")
            total += len(arg)
        if total > 8192:
            raise ShellCapabilityError("command arguments exceed size limit")
        if executable == "pwd" and args:
            raise ShellCapabilityError("pwd accepts no arguments")
        if executable == "echo" and any(arg.startswith("-") for arg in args):
            raise ShellCapabilityError("echo flags are not permitted")

    def run(self, executable: str, args: List[str]) -> ShellExecutionResult:
        self.validate_command(executable, args)
        command = [self.allowed_commands[executable], *args]
        environment = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}
        started = time.monotonic()
        process: subprocess.Popen[bytes] | None = None
        timed_out = False
        try:
            process = subprocess.Popen(
                command,
                cwd=self.root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                start_new_session=True,
                shell=False,
            )
            try:
                stdout, stderr = process.communicate(timeout=self.timeout_s)
            except subprocess.TimeoutExpired:
                timed_out = True
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                stdout, stderr = process.communicate()
        except OSError as exc:
            raise ShellCapabilityError("allowlisted executable could not start") from exc
        duration_ms = int((time.monotonic() - started) * 1000)
        stdout = stdout or b""
        stderr = stderr or b""
        output = stdout + stderr
        output_truncated = len(output) > self.max_output_bytes
        if output_truncated:
            remaining = self.max_output_bytes
            stdout = stdout[:remaining]
            remaining -= len(stdout)
            stderr = stderr[: max(0, remaining)]
        return ShellExecutionResult(
            executable=executable,
            args=tuple(args),
            returncode=None if timed_out else process.returncode if process else None,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
            timed_out=timed_out,
            output_truncated=output_truncated,
            duration_ms=duration_ms,
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_path(value: Optional[str]) -> Path:
    path = Path(value or settings.vesta_task_db_path)
    return path if path.is_absolute() else Path(settings.msb_home) / path


def _command_payload(request: VestaShellRequest) -> Dict[str, Any]:
    return {
        "executable": request.executable,
        "args": list(request.args),
        "expected_stdout": request.expected_stdout,
    }


def _command_hash(payload: Dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class VestaShellApprovalStore:
    """Durable exact-command approvals, separate from FILE_WRITE contracts."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = _db_path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS vesta_shell_approvals (
                    approval_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    bind_id TEXT NOT NULL UNIQUE,
                    command_json TEXT NOT NULL,
                    command_sha256 TEXT NOT NULL,
                    policy_version TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    decided_at TEXT,
                    decided_by TEXT,
                    reason TEXT
                )
                """
            )

    def submit(
        self,
        task_id: str,
        bind_id: str,
        command: Dict[str, Any],
        policy_version: str,
        expires_at: str,
    ) -> Dict[str, Any]:
        approval_id = f"shell_ack_{uuid.uuid4().hex}"
        command_json = json.dumps(command, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with sqlite3.connect(self.db_path) as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO vesta_shell_approvals(
                        approval_id, task_id, bind_id, command_json, command_sha256,
                        policy_version, expires_at, status, created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        approval_id,
                        task_id,
                        bind_id,
                        command_json,
                        _command_hash(command),
                        policy_version,
                        expires_at,
                        "PENDING",
                        _now(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ShellCapabilityError("shell approval already exists for this bind") from exc
        return self.get(approval_id)

    def get(self, approval_id: str) -> Dict[str, Any]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM vesta_shell_approvals WHERE approval_id=?",
                (approval_id,),
            ).fetchone()
        if row is None:
            raise ShellCapabilityError("unknown shell approval")
        return dict(row)

    def list(self, status: Optional[str] = None) -> list[Dict[str, Any]]:
        """All shell approvals, newest last; filter to one status (e.g. PENDING)."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            if status:
                rows = conn.execute(
                    "SELECT * FROM vesta_shell_approvals WHERE status=? ORDER BY created_at",
                    (status,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM vesta_shell_approvals ORDER BY created_at").fetchall()
        return [dict(row) for row in rows]

    def approve(self, approval_id: str, operator: str) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        expired = False
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status, expires_at FROM vesta_shell_approvals WHERE approval_id=?",
                (approval_id,),
            ).fetchone()
            if row is None:
                raise ShellCapabilityError("unknown shell approval")
            if row[0] != "PENDING":
                raise ShellCapabilityError("shell approval is already decided")
            try:
                expires = datetime.fromisoformat(str(row[1]).replace("Z", "+00:00"))
            except ValueError as exc:
                raise ShellCapabilityError("shell approval has invalid expiration") from exc
            if now >= expires:
                conn.execute(
                    "UPDATE vesta_shell_approvals SET status='EXPIRED', decided_at=?, reason=? WHERE approval_id=?",
                    (_now(), "approval expired", approval_id),
                )
                expired = True
            else:
                conn.execute(
                    "UPDATE vesta_shell_approvals SET status='APPROVED', decided_at=?, decided_by=? WHERE approval_id=? AND status='PENDING'",
                    (_now(), operator, approval_id),
                )
        if expired:
            raise ShellCapabilityError("shell approval expired")
        return self.get(approval_id)

    def reject(self, approval_id: str, operator: str, reason: str = "") -> Dict[str, Any]:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            updated = conn.execute(
                "UPDATE vesta_shell_approvals SET status='REJECTED', decided_at=?, decided_by=?, reason=? WHERE approval_id=? AND status='PENDING'",
                (_now(), operator, reason, approval_id),
            )
            if updated.rowcount != 1:
                raise ShellCapabilityError("unknown or already decided shell approval")
        return self.get(approval_id)

    def void(self, approval_id: str, reason: str = "") -> Dict[str, Any]:
        """Mark an APPROVED approval VOID because execution never completed
        validly (kill switch, postcondition failure, quarantine).

        VOID is terminal: ``approve`` refuses anything that is not PENDING and
        ``reject`` only touches PENDING rows, so a voided approval can never
        be re-decided into an execution.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            updated = conn.execute(
                "UPDATE vesta_shell_approvals SET status='VOID', decided_at=?, reason=? WHERE approval_id=? AND status='APPROVED'",
                (_now(), reason, approval_id),
            )
            if updated.rowcount != 1:
                raise ShellCapabilityError("shell approval is not in an APPROVED state")
        return self.get(approval_id)


class VestaShellService:
    """Submit and execute exact shell contracts only after owner approval."""

    def __init__(
        self,
        audit: AuditChainLike,
        tasks: VestaTaskStore,
        evidence: EvidenceStore,
        approvals: VestaShellApprovalStore,
        executor: ShellExecutor,
        kill_switch: KillSwitch,
    ) -> None:
        self.audit = audit
        self.tasks = tasks
        self.evidence = evidence
        self.approvals = approvals
        self.executor = executor
        self.kill_switch = kill_switch

    def _transition(
        self,
        task_id: str,
        state: str,
        event_ids: List[int],
        *,
        reason: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        task = self.tasks.transition(task_id, state, reason=reason, metadata=metadata)
        event_ids.append(
            self.audit.append(
                "vesta",
                "task.transition",
                {
                    "task_id": task_id,
                    "from_state": task["transitions"][-1]["from_state"],
                    "to_state": state,
                    "reason": reason,
                    "metadata": metadata or {},
                },
            ).seq
        )
        return task

    def submit(self, body: VestaShellRequest) -> Dict[str, Any]:
        bind = ABind.create(body.session, ["shell.exec"], ttl_seconds=300)
        command = _command_payload(body)
        request_evidence = self.evidence.record_json(
            {"bind_id": bind.bind_id, "task_id": bind.task_id, "command": command},
            "vesta.shell_request",
            {"bind_id": bind.bind_id, "task_id": bind.task_id, "command_sha256": _command_hash(command)},
        )
        evidence_refs = [request_evidence["evidence_id"]]
        self.tasks.create(bind, metadata={"command": command, "evidence_refs": evidence_refs})
        event_ids: List[int] = [
            self.audit.append(
                "vesta",
                "request.received",
                {
                    "bind_id": bind.bind_id,
                    "task_id": bind.task_id,
                    "actor": bind.actor,
                    "capability": "shell.exec",
                    "command_sha256": _command_hash(command),
                    "evidence_refs": evidence_refs,
                },
            ).seq
        ]
        self._transition(bind.task_id, "AUTHENTICATED", event_ids, metadata={"evidence_refs": evidence_refs})
        self._transition(bind.task_id, "PLANNED", event_ids, metadata={"planner": "vesta-shell"})
        decision = authorize_shell(body.executable, body.args, body.expected_stdout, bind)
        policy_evidence = self.evidence.record_json(
            decision.as_dict(),
            "vesta.shell_policy",
            {"bind_id": bind.bind_id, "task_id": bind.task_id},
        )
        evidence_refs.append(policy_evidence["evidence_id"])
        self.tasks.update_metadata(bind.task_id, {"evidence_refs": evidence_refs})
        event_ids.append(
            self.audit.append(
                "vesta",
                "authorization.decided",
                {
                    "bind_id": bind.bind_id,
                    "task_id": bind.task_id,
                    "evidence_refs": evidence_refs,
                    **decision.as_dict(),
                },
            ).seq
        )
        if decision.decision == "DENY":
            self._transition(
                bind.task_id,
                "DENIED",
                event_ids,
                reason="; ".join(decision.reasons),
                metadata={"evidence_refs": evidence_refs},
            )
            return {
                "status": "denied",
                "task_id": bind.task_id,
                "bind_id": bind.bind_id,
                "decision": decision.decision,
                "risk_class": decision.risk_class,
                "policy_version": bind.policy_version,
                "error": "; ".join(decision.reasons),
                "evidence_refs": evidence_refs,
                "audit_event_ids": event_ids,
            }
        approval = self.approvals.submit(bind.task_id, bind.bind_id, command, bind.policy_version, bind.deadline)
        self._transition(
            bind.task_id,
            "WAITING_APPROVAL",
            event_ids,
            reason="owner approval required for shell execution",
            metadata={"approval_id": approval["approval_id"], "evidence_refs": evidence_refs},
        )
        event_ids.append(
            self.audit.append(
                "vesta",
                "authorization.required",
                {
                    "bind_id": bind.bind_id,
                    "task_id": bind.task_id,
                    "approval_id": approval["approval_id"],
                    "decision": decision.decision,
                    "evidence_refs": evidence_refs,
                },
            ).seq
        )
        return {
            "status": "approval_required",
            "task_id": bind.task_id,
            "bind_id": bind.bind_id,
            "approval_id": approval["approval_id"],
            "command_sha256": approval["command_sha256"],
            "decision": decision.decision,
            "risk_class": decision.risk_class,
            "policy_version": bind.policy_version,
            "command": command,
            "evidence_refs": evidence_refs,
            "audit_event_ids": event_ids,
        }

    def approve_and_execute(
        self,
        approval_id: str,
        operator: str,
        *,
        signed_proof: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        # Verify-before-trust (security-hardening #3): the ledger must be
        # trustworthy before we act on prior state.
        trust = verify_trustworthy(self.audit)
        if not trust.get("valid"):
            raise ShellCapabilityError(f"audit chain not trustworthy — verify-before-trust failed: {trust.get('reason')}")
        approval = self.approvals.approve(approval_id, operator)
        task_id = str(approval["task_id"])
        command: Dict[str, Any] = {}
        event_ids: List[int] = [
            self.audit.append(
                "vesta",
                "approval.decided",
                {
                    "approval_id": approval_id,
                    "task_id": task_id,
                    "status": "APPROVED",
                    "operator": operator,
                    # Device-binding (security-hardening #6): a signed
                    # approval carries the device's cryptographic proof.
                    "signed_proof": signed_proof,
                },
            ).seq
        ]
        task = self.tasks.get(task_id)
        evidence_refs = list(task["metadata"].get("evidence_refs", []))
        try:
            parsed_command = json.loads(str(approval["command_json"]))
            if not isinstance(parsed_command, dict):
                raise ShellCapabilityError("approved shell command is not an object")
            command = parsed_command
            if _command_hash(command) != approval["command_sha256"]:
                raise ShellCapabilityError("approved shell command evidence changed")
            executable = str(command["executable"])
            args = [str(value) for value in command["args"]]
            expected_stdout = command.get("expected_stdout")
            self.executor.validate_command(executable, args)
            decision = authorize_shell(executable, args, expected_stdout, ABind.create("approval", ["shell.exec"]))
            if decision.decision != "REQUIRE_APPROVAL":
                raise ShellCapabilityError("approved command is no longer authorized")
            self._transition(task_id, "APPROVED", event_ids, metadata={"approval_id": approval_id})
            self._transition(task_id, "EXECUTING", event_ids, metadata={"approval_id": approval_id})
            if self.kill_switch.is_armed():
                self._transition(task_id, "QUARANTINED", event_ids, reason="kill switch armed")
                self._void_approval(approval_id, "kill switch armed", event_ids)
                return self._result("quarantined", task_id, approval_id, event_ids, evidence_refs, "kill switch armed")
            execution = self.executor.run(executable, args)
            output_evidence = self.evidence.record_json(
                execution.as_dict(),
                "vesta.shell_output",
                {"task_id": task_id, "approval_id": approval_id},
            )
            evidence_refs.append(output_evidence["evidence_id"])
            self.tasks.update_metadata(task_id, {"evidence_refs": evidence_refs})
            verification = {
                "ok": (
                    not execution.timed_out
                    and execution.returncode == 0
                    and not execution.output_truncated
                    and (expected_stdout is None or execution.stdout == expected_stdout)
                ),
                "method": "returncode_timeout_output_and_expected_stdout",
                "returncode": execution.returncode,
                "timed_out": execution.timed_out,
                "output_truncated": execution.output_truncated,
            }
            self._transition(
                task_id,
                "VERIFYING",
                event_ids,
                metadata={"verification": verification, "evidence_refs": evidence_refs},
            )
            if not verification["ok"]:
                raise ShellCapabilityError("shell postcondition verification failed")
            event_ids.append(
                self.audit.append(
                    "vesta",
                    "execution.verified",
                    {
                        "task_id": task_id,
                        "approval_id": approval_id,
                        "verification": verification,
                        "evidence_refs": evidence_refs,
                    },
                ).seq
            )
            self._transition(task_id, "COMPLETED", event_ids, metadata={"evidence_refs": evidence_refs})
            return {
                "status": "completed",
                "task_id": task_id,
                "approval_id": approval_id,
                "execution": execution.as_dict(),
                "verification": verification,
                "evidence_refs": evidence_refs,
                "audit_event_ids": event_ids,
            }
        except (ShellCapabilityError, EvidenceError, TaskLifecycleError, json.JSONDecodeError) as exc:
            try:
                current = self.tasks.get(task_id)["state"]
                if current in {"EXECUTING", "VERIFYING"}:
                    self._transition(task_id, "RECOVERING", event_ids, reason=str(exc))
                current = self.tasks.get(task_id)["state"]
                if current in {"WAITING_APPROVAL", "APPROVED", "RECOVERING"}:
                    self._transition(
                        task_id,
                        "QUARANTINED",
                        event_ids,
                        reason="shell execution failed; operator review required",
                        metadata={"evidence_refs": evidence_refs},
                    )
            # Best-effort quarantine recording: ignore TaskLifecycleError
            # when the task is already in a terminal state. The original
            # shell failure above stays surfaced; a failed bookkeeping
            # transition should not mask it.
            except TaskLifecycleError:
                pass
            self._void_approval(approval_id, str(exc), event_ids)
            event_ids.append(
                self.audit.append(
                    "vesta",
                    "execution.failed",
                    {
                        "task_id": task_id,
                        "approval_id": approval_id,
                        "error": str(exc),
                        "evidence_refs": evidence_refs,
                    },
                ).seq
            )
            return self._result("quarantined", task_id, approval_id, event_ids, evidence_refs, str(exc))

    def _void_approval(self, approval_id: str, reason: str, event_ids: List[int]) -> None:
        """Best-effort VOID of an APPROVED approval whose task quarantined.

        Never masks the original execution failure: a bookkeeping failure
        here is swallowed, with the task quarantine as the authoritative
        record.
        """
        try:
            self.approvals.void(approval_id, reason)
            event_ids.append(
                self.audit.append(
                    "vesta",
                    "approval.voided",
                    {
                        "approval_id": approval_id,
                        "task_id": str(self.approvals.get(approval_id)["task_id"]),
                        "reason": reason,
                    },
                ).seq
            )
        except ShellCapabilityError:
            pass

    @staticmethod
    def _result(
        status: str,
        task_id: str,
        approval_id: str,
        event_ids: List[int],
        evidence_refs: List[str],
        error: str,
    ) -> Dict[str, Any]:
        return {
            "status": status,
            "task_id": task_id,
            "approval_id": approval_id,
            "error": error,
            "evidence_refs": evidence_refs,
            "audit_event_ids": event_ids,
        }

    def reject(self, approval_id: str, operator: str, reason: str = "") -> Dict[str, Any]:
        approval = self.approvals.reject(approval_id, operator, reason)
        event_ids: List[int] = []
        self._transition(
            str(approval["task_id"]),
            "DENIED",
            event_ids,
            reason=reason or "owner rejected shell execution",
        )
        event_ids.append(
            self.audit.append(
                "vesta",
                "approval.rejected",
                {
                    "approval_id": approval_id,
                    "task_id": approval["task_id"],
                    "operator": operator,
                    "reason": reason,
                },
            ).seq
        )
        return {
            "status": "rejected",
            "approval_id": approval_id,
            "task_id": approval["task_id"],
            "audit_event_ids": event_ids,
        }
