#!/usr/bin/env python3
"""The BLE001 backlog gate — a declared backlog that can only shrink honestly.

Why this exists
---------------
On 2026-09-23 BLE001 (flake8-blind-except) was switched on, and 130 `# noqa:
BLE001` annotations that had been *inert* for months became load-bearing. The
166 sites that had no such annotation were declared in `pyproject.toml`'s
`[tool.ruff.lint.per-file-ignores]` rather than by turning the rule off, so the
remaining work stayed visible and countable.

That declaration has a hole, and it is not the one it looks like.

**Growth is already blocked.** A file with a blind `except Exception:` that is
neither annotated nor declared fails `ruff check` on its own. Ruff does not need
help there.

The two things ruff cannot see are the ones that decay a backlog:

* **Staleness.** A row whose file no longer has any blind catches is invisible.
  Ruff is satisfied — the suppression suppresses nothing. Nothing ever removes
  the row, so the backlog can never be seen to shrink, and a file that was
  triaged still reads as outstanding. (Verified while writing this: adding a
  `per-file-ignores` row for a file with zero blind catches produced
  "All checks passed!".)
* **Silent re-addition.** A newly declared file needs no reason. Nothing
  distinguishes "we considered this and the blanket catch is right" from "ruff
  went red and the row was added to make it green." Both leave the same one-line
  config change.

This gate closes those two. It reads the declaration, measures what is actually
there, and fails when the two disagree — so the backlog is a list someone is
accountable for rather than a config that accumulates.

What it checks
--------------
R0 **armed** — `BLE001` is still in `[tool.ruff.lint].select`. If it is not, this
   gate is checking a rule nothing enforces, which is worse than no gate.
R1 **agreement, both directions** — every declared `src/` row has exactly one
   ledger entry, and no ledger entry names a file that is not declared. A row
   that appears without a ledger entry is the silent re-addition above.
R2 **reasons** — every group carries a non-empty reason, and every declared
   non-`src/` glob is accounted for in `class_exemptions` with one. A class
   exemption is a claim about a whole class of files, so it needs the argument.
R3 **staleness** — a declared file with zero live blind catches must have its row
   deleted. This is the check that makes burn-down legible.
R4 **ceilings** — declared files and covered sites must not exceed the ledger's
   ceilings. Growth past the ratchet has to be a deliberate edit to the ledger,
   not a side effect of fixing a different problem.
R5 **coverage** — a file with blind catches that is declared neither here nor by
   an inline reason is reported (ruff also fails on it; the message here says
   which file and how many, which is what you actually want to read).

How "staleness" is measured, and why `--isolated` matters
--------------------------------------------------------
`ruff check src/` honours `per-file-ignores`, so it reports nothing for a
declared file — including a stale one. Running the same check with `--isolated`
ignores configuration files while still honouring inline `# noqa`, which yields
exactly the set of blind catches the declaration is covering. Measured on
2026-09-23: **152 sites across 60 files** isolated, **0** with the config
applied. The difference *is* the backlog. A declared file whose isolated count
is 0 is suppressing nothing, and that is a finding.

Honest limits
-------------
* **It cannot tell a good reason from a plausible one.** A group reason is prose
  a human wrote; this checks that one exists and that the file is accounted for,
  not that the reasoning is sound.
* **The group reasons are a bulk classification, not 152 individual readings.**
  Their stated basis is a measured one — 192 of 193 blind catches in these files
  already log, re-raise, or convert to a reported value, with exactly 1 silent
  site (inline-annotated) — plus four sampled files. Per-file refinement is
  burn-down work, and the ledger says so.
* **Ceilings only block growth.** They do not force a burn-down, and they go
  slack if nobody lowers them. `--update` rewrites them from live measurement
  after a batch, which is the intended maintenance step, and the report prints
  the gap so slack is visible rather than inferred.
* **Ruff must be importable from this interpreter.** The gate shells out to
  `python -m ruff`; if ruff is missing it says so instead of passing.
* It reads `src/` only. Tests are a declared class exemption, by argument, not a
  backlog — see the ledger.

Usage
-----
    python3 scripts/ble001_backlog.py              # check (offline, default)
    python3 scripts/ble001_backlog.py --census     # print ledger stanzas for anything new
    python3 scripts/ble001_backlog.py --update     # rewrite ceilings from live measurement
    python3 scripts/ble001_backlog.py --json       # machine-readable result
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
LEDGER = ROOT / "config" / "ble001-backlog.json"
SRC = "src"
RULE = "BLE001"


class GateError(Exception):
    """Something is wrong with the inputs, not with the tree."""


def label(path: Path) -> str:
    """Path as it should read in a message: repo-relative when it is in the repo.

    `relative_to` raises when the path is outside the repository (a staged copy,
    an operator pointing the gate at a file elsewhere), and an error path that
    itself crashes hides the failure it was reporting.
    """
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


# ---------------------------------------------------------------------------
# The declaration, from the two places it lives
# ---------------------------------------------------------------------------


def ruff_config() -> dict:
    """`[tool.ruff.lint]` from pyproject, or a clear failure."""
    try:
        data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise GateError(f"cannot read {label(PYPROJECT)}: {exc}") from exc
    try:
        return data["tool"]["ruff"]["lint"]
    except KeyError as exc:
        raise GateError("pyproject.toml has no [tool.ruff.lint] table") from exc


def declared() -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """(concrete file rows, glob rows) from `per-file-ignores`."""
    ignores = ruff_config().get("per-file-ignores", {})
    concrete = {k: v for k, v in ignores.items() if "*" not in k}
    globs = {k: v for k, v in ignores.items() if "*" in k}
    return concrete, globs


def ledger() -> dict:
    try:
        return json.loads(LEDGER.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GateError(f"cannot read {label(LEDGER)}: {exc}") from exc


def ledger_files(data: dict) -> dict[str, str]:
    """file -> the reason of the group that owns it (duplicates are a finding)."""
    out: dict[str, str] = {}
    for group in data.get("groups", ()):
        for rel in group.get("files", ()):
            out[rel] = group.get("reason", "")
    return out


# ---------------------------------------------------------------------------
# Live measurement
# ---------------------------------------------------------------------------


def live_sites() -> dict[str, int]:
    """Blind catches per file, ignoring pyproject so the declaration is visible.

    `--isolated` drops configuration files (so `per-file-ignores` cannot hide
    anything) while still honouring inline `# noqa`. That is exactly the set the
    declaration is covering.
    """
    proc = subprocess.run(
        [
            sys.executable, "-m", "ruff", "check",
            "--isolated", "--select", RULE,
            "--output-format", "concise", SRC,
        ],
        cwd=ROOT, capture_output=True, text=True,
    )
    if "No module named" in proc.stderr or "No module named" in proc.stdout:
        raise GateError(
            "ruff is not importable from this interpreter — the gate cannot "
            "measure the backlog. Install the dev extras (ruff is pinned there)."
        )
    counts: dict[str, int] = {}
    for line in proc.stdout.splitlines():
        if RULE not in line or ":" not in line:
            continue
        path = line.split(":")[0].strip()
        counts[path] = counts.get(path, 0) + 1
    if not counts and proc.returncode not in (0, 1):
        raise GateError(f"ruff failed unexpectedly (exit {proc.returncode}): {proc.stderr.strip()}")
    return counts


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------


@dataclass
class Result:
    findings: list[str] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def fail(self, message: str) -> None:
        self.findings.append(message)


def check_armed(result: Result) -> None:
    """R0 — a gate over a rule nobody enforces is worse than no gate."""
    select = ruff_config().get("select", [])
    if RULE not in select:
        result.fail(
            f"{RULE} is not in [tool.ruff.lint].select — this gate would be checking a "
            f"rule nothing enforces, and every row below it is decoration"
        )
    result.stats["rule armed"] = int(RULE in select)


def check_agreement(result: Result, concrete: dict, ledger_map: dict) -> None:
    """R1 — both directions: every row is explained, every explanation is used."""
    undeclared_files = sorted(set(ledger_map) - set(concrete))
    if undeclared_files:
        result.fail(
            "ledger names files with no per-file-ignores row (the row was deleted "
            "without updating the ledger — remove the entry, or restore the row): "
            + ", ".join(undeclared_files)
        )
    unexplained = sorted(set(concrete) - set(ledger_map))
    if unexplained:
        result.fail(
            "declared in pyproject.toml with no ledger entry — a suppression with no "
            "written reason is exactly the silent re-addition this gate exists for. "
            "Add each file to a group in config/ble001-backlog.json, or annotate the "
            "sites and drop the row: " + ", ".join(unexplained)
        )
    result.stats["declared files"] = len(concrete)
    result.stats["ledger files"] = len(ledger_map)


def check_reasons(result: Result, data: dict, globs: dict) -> None:
    """R2 — a group with no reason is a suppression with no argument."""
    groups = data.get("groups", ())
    if not groups:
        result.fail("the ledger declares no groups — nothing justifies the backlog")
    for group in groups:
        gid = group.get("id", "<unnamed>")
        if not str(group.get("reason", "")).strip():
            result.fail(f"group {gid!r} has no reason")
        if not group.get("files"):
            result.fail(f"group {gid!r} lists no files — an empty group is dead weight")

    exemptions = data.get("class_exemptions", {})
    for pattern in sorted(set(globs) - set(exemptions)):
        result.fail(
            f"pyproject declares a class exemption {pattern!r} that the ledger does "
            f"not argue for — a glob suppresses a whole class, so it needs a reason"
        )
    for pattern in sorted(set(exemptions) - set(globs)):
        result.fail(
            f"ledger argues for a class exemption {pattern!r} that pyproject does not "
            f"declare — the argument is about nothing"
        )
    result.stats["class exemptions"] = len(exemptions)
    result.stats["groups"] = len(groups)


def check_stale(result: Result, concrete: dict, live: dict[str, int]) -> None:
    """R3 — a row suppressing nothing is a row that must go."""
    stale = [rel for rel in sorted(concrete) if live.get(rel, 0) == 0]
    for rel in stale:
        result.fail(
            f"{rel} is declared in per-file-ignores but has no live {RULE} "
            f"violations — the row suppresses nothing. Delete the row and its "
            f"ledger entry: a backlog that cannot be seen to shrink is not a "
            f"backlog, it is a permanent exemption."
        )
    result.stats["stale rows"] = len(stale)


def check_ceilings(result: Result, data: dict, concrete: dict, live: dict[str, int]) -> None:
    """R4 — growth past the ratchet must be a deliberate ledger edit."""
    ceilings = data.get("ceilings", {})
    covered = {rel: live.get(rel, 0) for rel in concrete}
    sites = sum(covered.values())
    result.stats["covered sites"] = sites
    for name, measured in (("files", len(concrete)), ("sites", sites)):
        ceiling = ceilings.get(name)
        if not isinstance(ceiling, int):
            result.fail(f"the ledger declares no integer ceiling for {name!r}")
        elif measured > ceiling:
            result.fail(
                f"the backlog grew past its ceiling: {name} measured {measured}, "
                f"ceiling {ceiling}. Declaring a new file (or new sites in a declared "
                f"file) is a deliberate act — raise the ceiling in "
                f"config/ble001-backlog.json with a reason, or fix the sites."
            )
        else:
            result.stats[f"ceiling headroom ({name})"] = ceiling - measured
    if ceilings.get("files") == len(concrete) and sites < ceilings.get("sites", 0):
        result.notes.append(
            f"ceilings are slack ({sites} sites vs {ceilings.get('sites')} declared); "
            f"run --update after a burn-down batch to keep the ratchet tight"
        )


def check_coverage(result: Result, concrete: dict, live: dict[str, int]) -> None:
    """R5 — anything blind and undeclared. Ruff fails on this too; this says where."""
    undeclared = sorted(set(live) - set(concrete))
    for rel in undeclared:
        result.fail(
            f"{rel} has {live[rel]} live {RULE} violation(s) and is declared neither "
            f"by an inline reason nor in per-file-ignores. Annotate each site, or add "
            f"the file to a group in the ledger and a row in pyproject.toml."
        )


def run() -> Result:
    result = Result()
    concrete, globs = declared()
    data = ledger()
    check_armed(result)
    check_reasons(result, data, globs)
    live = live_sites()
    check_agreement(result, concrete, ledger_files(data))
    check_stale(result, concrete, live)
    check_ceilings(result, data, concrete, live)
    check_coverage(result, concrete, live)
    return result


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------


def census() -> int:
    """Print a ready-to-paste stanza for anything blind and undeclared."""
    concrete, _ = declared()
    live = live_sites()
    undeclared = {rel: n for rel, n in sorted(live.items()) if rel not in concrete}
    if not undeclared:
        print(f"[ble001] census: every live {RULE} site is declared or inline-annotated")
        return 0
    print(
        f"[ble001] census: {len(undeclared)} declaration(s) missing — decide the reason "
        f"before pasting; a row without one is the failure this gate exists for:"
    )
    for rel, count in undeclared.items():
        print(f'\n    {{"id": "TODO", "reason": "", "files": ["{rel}"]}},   # {count} site(s)')
    print("\nThen add a [tool.ruff.lint.per-file-ignores] row for each file above:")
    for rel in undeclared:
        print(f'    "{rel}" = ["{RULE}"]')
    print(
        "and raise the ceilings in config/ble001-backlog.json if this is "
        "genuine growth — raising them is the deliberate act that keeps the "
        "ratchet meaningful.\n"
    )
    return 1


def update_ceilings() -> int:
    """Rewrite the ceilings from live measurement (after a burn-down batch)."""
    data = ledger()
    concrete, _ = declared()
    live = live_sites()
    sites = sum(live.get(rel, 0) for rel in concrete)
    data["ceilings"] = {
        "files": len(concrete),
        "sites": sites,
        "note": (
            "The ratchet. Exceeding either fails the gate; lower them when you burn "
            "the backlog down (`--update` rewrites them from live measurement)."
        ),
    }
    LEDGER.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"[ble001] ceilings rewritten: {len(concrete)} files, {sites} sites")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="BLE001 backlog gate (see module docstring)")
    parser.add_argument("--census", action="store_true", help="propose stanzas for undeclared sites")
    parser.add_argument("--update", action="store_true", help="rewrite ceilings from live measurement")
    parser.add_argument("--json", action="store_true", help="machine-readable result")
    args = parser.parse_args()

    try:
        if args.census:
            return census()
        if args.update:
            return update_ceilings()
        result = run()
    except GateError as exc:
        print(f"[ble001] FAIL: {exc}")
        return 1

    if args.json:
        print(json.dumps({"findings": result.findings, "stats": result.stats}, indent=2))
        return 1 if result.findings else 0

    for note in result.notes:
        print(f"[ble001] {note}")
    if result.findings:
        print(f"[ble001] FAIL: {len(result.findings)} finding(s) in the BLE001 backlog:")
        for finding in result.findings:
            print(f"  - {finding}")
        return 1
    summary = ", ".join(f"{value} {key}" for key, value in result.stats.items())
    print(f"[ble001] PASS: {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
