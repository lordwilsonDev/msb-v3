"""Guardian orchestrator (doc 1 §7, pruned to OBSERVE — doc 4 §4).

Sequence: lock -> forensics -> (dirty tree? deterministic ESCALATE : reason)
-> ledger -> kpi -> emit -> exit. No mutation path exists.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from . import kpi, ledger, reasoning
from .config import GuardianConfig
from .forensics import collect
from .reasoning import Escalation, GuardianResult

EXIT_OK = 0
EXIT_ESCALATE = 1
EXIT_CRITICAL = 2


def _run_id() -> str:
    return "guardian-" + datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return isinstance(pid, int) and pid > 0 and _perm_means_alive()
    except OSError:
        return False
    return True


def _perm_means_alive() -> bool:
    return True  # EPERM => a process with that pid exists under another user


class LockHeld(RuntimeError):
    pass


def _acquire_lock(cfg: GuardianConfig, run_id: str) -> Path:
    d = cfg.ledger.local_state_dir
    d.mkdir(parents=True, exist_ok=True)
    lock = d / "lock.json"
    if lock.exists():
        try:
            prev = json.loads(lock.read_text(encoding="utf-8"))
            pid = int(prev.get("pid", -1))
        except (ValueError, OSError):
            pid = -1
        if pid > 0 and _pid_alive(pid):
            raise LockHeld(f"CONCURRENT_GUARDIAN: pid {pid} holds {lock}")
    lock.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "pid": os.getpid(),
                "started_at": datetime.now(tz=timezone.utc).isoformat(),
                "hostname": os.uname().nodename,
            }
        ),
        encoding="utf-8",
    )
    return lock


_CLEAN_CONTROLS = ["schema_valid", "repo_boundary", "no_mutation", "secret_guard"]


def _wt(forensics: dict[str, object]) -> dict[str, object]:
    git = forensics.get("git", {})
    wt = git.get("working_tree", {}) if isinstance(git, dict) else {}
    return wt if isinstance(wt, dict) else {}


def _wt_list(wt: dict[str, object], key: str) -> list[str]:
    v = wt.get(key)
    return [str(x) for x in v] if isinstance(v, list) else []


def _dirty_tree_escalation(run_id: str, forensics: dict[str, object]) -> GuardianResult:
    wt = _wt(forensics)
    uns = _wt_list(wt, "unstaged") or _wt_list(wt, "modified")
    unt = _wt_list(wt, "untracked")
    return GuardianResult(
        run_id=run_id,
        decision="ESCALATE",
        summary=(
            f"Working tree dirty after ignorable-globs filter: {len(uns)} unstaged, "
            f"{len(unt)} untracked, none attributable to the Guardian (doc 1 §12)."
        ),
        escalations=[
            Escalation(
                reason="AMBIGUOUS_WORKING_TREE",
                blocking=True,
                evidence_ref="git.working_tree",
                detail=f"unstaged={uns} untracked={unt}",
            )
        ],
        controls_passed=list(_CLEAN_CONTROLS),
        tests={"passed": 0, "failed": 0, "skipped": 0, "not_run": "all"},
    )


def _staged_pending_commit(run_id: str, forensics: dict[str, object]) -> GuardianResult:
    staged = _wt_list(_wt(forensics), "staged")
    return GuardianResult(
        run_id=run_id,
        decision="PROPOSE",
        summary=(
            f"{len(staged)} file(s) fully staged, work-dir clean — ready for a human commit. "
            f"Not AMBIGUOUS_WORKING_TREE: the change is attributable and contained."
        ),
        escalations=[
            Escalation(
                reason="STAGED_PENDING_COMMIT",
                blocking=False,
                evidence_ref="git.working_tree.staged",
                detail=f"staged={staged}",
            )
        ],
        controls_passed=list(_CLEAN_CONTROLS),
        tests={"passed": 0, "failed": 0, "skipped": 0, "not_run": "all"},
    )


def execute(config_path: str | Path, *, dry_run: bool = False) -> tuple[GuardianResult, int]:
    cfg = GuardianConfig.load(config_path)
    if cfg.mode != "OBSERVE":
        raise SystemExit(f"v1 supports OBSERVE only; config says {cfg.mode!r}")

    run_id = _run_id()
    started = time.monotonic()
    lock: Path | None = None
    try:
        lock = _acquire_lock(cfg, run_id)
    except LockHeld as exc:
        result = GuardianResult(
            run_id=run_id,
            decision="ESCALATE",
            summary=str(exc),
            escalations=[Escalation(reason="CONCURRENT_GUARDIAN", blocking=True, detail=str(exc))],
            controls_passed=["no_mutation"],
        )
        ledger.write(cfg, {"run_id": run_id, "mode": cfg.mode, "repo": cfg.repo_path.name}, result, dry_run=dry_run)
        return result, EXIT_ESCALATE

    try:
        forensics = collect(cfg, run_id)
        wt = _wt(forensics)
        clean = bool(wt.get("clean_after_filter", False))
        staged_only = bool(wt.get("staged_only", False))

        if not clean and not staged_only:
            result = _dirty_tree_escalation(run_id, forensics)
        elif staged_only:
            result = _staged_pending_commit(run_id, forensics)
        elif time.monotonic() - started > cfg.timebox_seconds:
            result = GuardianResult(
                run_id=run_id,
                decision="ESCALATE",
                summary="Timebox exhausted before the reasoning step.",
                escalations=[Escalation(reason="TIMEBOX_EXHAUSTED", blocking=True)],
                controls_passed=["no_mutation"],
            )
        else:
            result = reasoning.classify(cfg, run_id, forensics)

        runtime_s = round(time.monotonic() - started, 2)
        ledger.write(cfg, forensics, result, dry_run=dry_run)
        kpi.record(cfg, result, runtime_s=runtime_s, dry_run=dry_run)
        exit_code = EXIT_ESCALATE if result.decision == "ESCALATE" else EXIT_OK
        return result, exit_code
    finally:
        if lock is not None and lock.exists():
            lock.unlink()


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="msb-v3-guardian", description="S-AOS Guardian (OBSERVE)")
    ap.add_argument("--config", default="config/guardian.toml")
    ap.add_argument("--dry-run", action="store_true", help="write under var/guardian/dry-run/ instead of the vault")
    args = ap.parse_args(argv)

    result, code = execute(args.config, dry_run=args.dry_run)
    print(result.model_dump_json(indent=2, by_alias=True))
    return code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
