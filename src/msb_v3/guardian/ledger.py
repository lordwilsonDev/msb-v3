"""Write the run record to the Obsidian vault (doc 4 §7).

Layout::

    <vault_dir>/ledger/<run_id>/forensics.json
    <vault_dir>/ledger/<run_id>/run.json
    <vault_dir>/ledger/<run_id>/run.md
    <inbox_dir>/S-AOS-ESCALATION_<date>_<REASON>.md   (only on a blocking escalation)

``dry_run`` redirects everything under ``<local_state_dir>/dry-run/`` so a
rehearsal never touches the vault.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .config import GuardianConfig
from .reasoning import GuardianResult

_SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def _target_dirs(cfg: GuardianConfig, dry_run: bool) -> tuple[Path, Path]:
    if dry_run:
        root = cfg.ledger.local_state_dir / "dry-run"
        return root / "ledger", root / "inbox"
    return cfg.ledger.vault_dir / "ledger", cfg.ledger.inbox_dir


def _run_md(forensics: dict[str, object], result: GuardianResult) -> str:
    git = forensics.get("git", {})
    wt = git.get("working_tree", {}) if isinstance(git, dict) else {}
    head = git.get("head", "?") if isinstance(git, dict) else "?"
    lines = [
        f"# Guardian run — {result.run_id}",
        "",
        f"**Mode:** {forensics.get('mode', '?')}  ·  **Repo:** {forensics.get('repo', '?')} @ `{head}`",
        f"**Decision:** `{result.decision}`  ·  **State hash:** `{forensics.get('start_state_hash', '?')}`",
        "",
        f"> {result.summary}" if result.summary else "",
        "",
    ]
    if isinstance(wt, dict) and not wt.get("clean_after_filter", True):
        lines += [
            "## Working tree (after ignorable-globs filter)",
            "",
            f"- modified: {', '.join(wt.get('modified', [])) or '—'}",
            f"- untracked: {', '.join(wt.get('untracked', [])) or '—'}",
            f"- filtered out by config: {', '.join(wt.get('ignored_by_config', [])) or '—'}",
            "",
        ]
    if result.findings:
        lines += ["## Findings", "", "| Sev | Type | Class | Statement |", "|---|---|---|---|"]
        for f in sorted(result.findings, key=lambda x: _SEV_ORDER.get(x.severity, 9)):
            lines.append(
                f"| {f.severity} | {f.claim_type} | {f.cls} | {f.statement.replace(chr(10), ' ')} |"
            )
        lines.append("")
    if result.proposals:
        lines += ["## Proposals (written, not acted — OBSERVE)", ""]
        for p in result.proposals:
            lines.append(f"- **{p.intent_id}** `{p.change_class}` — {p.objective}")
        lines.append("")
    if result.escalations:
        lines += ["## Escalations", ""]
        for e in result.escalations:
            tag = "blocking" if e.blocking else "non-blocking"
            lines.append(f"- **{e.reason}** ({tag}) — {e.detail or e.evidence_ref}")
        lines.append("")
    if result.controls_passed:
        lines += ["## Controls passed", "", " · ".join(f"`{c}`" for c in result.controls_passed), ""]
    return "\n".join(x for x in lines if x is not None)


def _inbox_note(cfg_repo: str, result: GuardianResult, run_dir: Path) -> tuple[str, str]:
    blk = next((e for e in result.escalations if e.blocking), None)
    reason = blk.reason if blk else "ESCALATION"
    date = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    name = f"S-AOS-ESCALATION_{date}_{reason.replace('_', '-')}.md"
    body = "\n".join(
        [
            "---",
            "tags: [s-aos, guardian, escalation]",
            f"date: {date}",
            f"run_id: {result.run_id}",
            f"reason: {reason}",
            f"repo: {cfg_repo}",
            "---",
            "",
            f"# S-AOS Guardian escalation — {reason}",
            "",
            f"**Decision:** `{result.decision}`  ·  **Full record:** `{run_dir}`",
            "",
            result.summary,
            "",
        ]
    )
    return name, body


def write(
    cfg: GuardianConfig,
    forensics: dict[str, object],
    result: GuardianResult,
    *,
    dry_run: bool = False,
) -> dict[str, str]:
    ledger_root, inbox_root = _target_dirs(cfg, dry_run)
    run_dir = ledger_root / result.run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    (run_dir / "forensics.json").write_text(
        json.dumps(forensics, indent=2, sort_keys=False), encoding="utf-8"
    )
    (run_dir / "run.json").write_text(
        result.model_dump_json(indent=2, by_alias=True), encoding="utf-8"
    )
    (run_dir / "run.md").write_text(_run_md(forensics, result), encoding="utf-8")

    written = {"run_dir": str(run_dir)}

    if any(e.blocking for e in result.escalations):
        inbox_root.mkdir(parents=True, exist_ok=True)
        name, body = _inbox_note(str(forensics.get("repo", "?")), result, run_dir)
        (inbox_root / name).write_text(body, encoding="utf-8")
        written["inbox_note"] = str(inbox_root / name)

    return written
