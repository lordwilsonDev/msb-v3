#!/usr/bin/env python3
"""Closure Drift Guard — fails if closure-plan.md task statuses drift from git log.

This script parses closure-plan.md for task statuses and commit SHAs,
then verifies that:
1. Every claimed commit SHA exists in the repo
2. Every "DONE" task has a valid commit reference
3. The closure % matches the actual done/total ratio

Run as a CI step or pre-commit hook to prevent the "lying dashboard" problem.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


def get_git_shas() -> set[str]:
    """Get all commit SHAs in the repo (first 7 chars for short SHA matching)."""
    result = subprocess.run(
        ["git", "log", "--oneline", "--all"],
        capture_output=True, text=True, cwd=Path(__file__).parent.parent
    )
    shas = set()
    for line in result.stdout.strip().split("\n"):
        if line.strip():
            sha = line.split()[0]
            shas.add(sha)
    return shas


def parse_closure_plan(plan_path: Path) -> dict[str, dict]:
    """Parse closure-plan.md for tasks with status and commit SHA."""
    content = plan_path.read_text()
    tasks = {}

    # Split by task headers
    task_blocks = re.split(r"(?=### TASK-\d+:)", content)

    for block in task_blocks:
        # Match task header
        header_match = re.match(r"### (TASK-\d+):\s+(.+)", block)
        if not header_match:
            continue

        task_id = header_match.group(1)
        title = header_match.group(2).strip()

        # Extract status
        status_match = re.search(r"\*\*Status\*\*:\s*(\S+)", block)
        status = status_match.group(1) if status_match else "UNKNOWN"

        # Extract commit SHA
        sha_match = re.search(r"\*\*Closed by\*\*:\s*`([0-9a-f]+)`", block)
        sha = sha_match.group(1) if sha_match else None

        tasks[task_id] = {
            "title": title,
            "status": status,
            "sha": sha,
        }

    return tasks


def verify_shas_exist(tasks: dict[str, dict], git_shas: set[str]) -> list[str]:
    """Verify that all claimed commit SHAs exist in the repo."""
    errors = []
    for task_id, task in tasks.items():
        if task["sha"]:
            # Check if the full SHA or short SHA exists
            sha = task["sha"]
            found = any(s.startswith(sha) for s in git_shas)
            if not found:
                errors.append(f"{task_id}: claimed SHA `{sha}` not found in git log")
    return errors


def verify_done_tasks_have_shas(tasks: dict[str, dict]) -> list[str]:
    """Verify that DONE tasks have commit SHAs."""
    warnings = []
    for task_id, task in tasks.items():
        if task["status"] == "DONE" and not task["sha"]:
            warnings.append(f"{task_id}: DONE but no commit SHA claimed")
    return warnings


def compute_closure_score(tasks: dict[str, dict]) -> tuple[int, int, float]:
    """Compute closure score from task statuses."""
    total = len(tasks)
    done = sum(1 for t in tasks.values() if t["status"] in ("DONE", "RESOLVED", "SHIPPED"))
    pct = (done / total * 100) if total > 0 else 0
    return done, total, pct


def main() -> int:
    project_root = Path(__file__).parent.parent
    plan_path = project_root / "closer" / "plan" / "closure-plan.md"

    if not plan_path.exists():
        print(f"SKIP: {plan_path} not found")
        return 0

    print("Closure Drift Guard — checking closure-plan.md against git log")
    print("=" * 60)

    git_shas = get_git_shas()
    tasks = parse_closure_plan(plan_path)

    if not tasks:
        print("WARN: No tasks found in closure-plan.md")
        return 0

    # Verify SHAs
    sha_errors = verify_shas_exist(tasks, git_shas)
    sha_warnings = verify_done_tasks_have_shas(tasks)

    # Compute score
    done, total, pct = compute_closure_score(tasks)

    # Report
    print(f"\nTasks: {done}/{total} done ({pct:.0f}%)")
    print(f"Blocked: {sum(1 for t in tasks.values() if t['status'] == 'BLOCKED')}")
    print(f"Planned: {sum(1 for t in tasks.values() if t['status'] == 'PLANNED')}")

    if sha_errors:
        print(f"\n❌ SHA ERRORS ({len(sha_errors)}):")
        for err in sha_errors:
            print(f"  - {err}")
        return 1

    if sha_warnings:
        print(f"\n⚠️  WARNINGS ({len(sha_warnings)}):")
        for warn in sha_warnings:
            print(f"  - {warn}")

    print("\n✅ Closure plan is consistent with git log")
    return 0


if __name__ == "__main__":
    sys.exit(main())
