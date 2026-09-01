#!/usr/bin/env python3
"""P0.1 secret-prevention gate (AIB-001 §5) — fail-closed secret scanner.

Two modes:

    python scripts/scan-secrets.py --staged     # pre-commit: added staged lines
    python scripts/scan-secrets.py --tree       # CI / full tree: tracked file contents
    python scripts/scan-secrets.py --git-diff BASE  # added lines vs BASE ref

Exit 0 = clean, 1 = matches found (blocking), 2 = usage/scan error.

Design rules (the blueprint's adversarial requirements):

- **Entropy over prefix.** A match counts only if the token body mixes at
  least two character classes (lower, upper, digit).  Placeholders like
  ``tvly-dev-AAAAAAAAAAAAAAAAAAAAAAAAAAAAA`` (one class) and ``sk-test``
  (too short) are deliberately NOT flagged — they are the repo's own test
  fixtures and the wrongness engine's fake-key inputs.  A real key mixes
  classes (``tvly-dev-<REDACTED>-7lv7...``).
- **Fail closed.** Any staged match blocks the commit; any tree match fails
  the CI step.  The known historical offender (``.claude/settings.local.json``)
  is exactly what the --tree mode exists to keep failing until it is purged.
- **Explicit override only.** ``# pragma: allowlist-secret`` on the line, or
  ``MSB_SECRET_SCAN_OFF=1`` in the environment, bypasses — never silence by
  default.  ``--tree`` ignores the pragma (a committed allowlist marker is
  itself a claim that must survive review); pass ``--allow-pragma`` to
  enable it.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

# family -> (regex-group-safe name, pattern).  Bodies are checked for
# class-mixing (entropy rule): a token matches only if >= 2 character
# classes appear, so single-class placeholders (the repo's own fake-key
# fixtures) pass deliberately.
_PATTERNS: list[tuple[str, str, re.Pattern[str]]] = [
    ("tavily", "tavily", re.compile(r"tvly-[A-Za-z0-9_-]{20,}")),
    ("openai-style", "openai_style", re.compile(r"\bsk-[A-Za-z0-9]{32,}")),
    ("aws", "aws", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github-pat", "github_pat", re.compile(r"ghp_[A-Za-z0-9]{36}")),
    ("google-api", "google_api", re.compile(r"AIza[0-9A-Za-z_-]{35}")),
    ("slack", "slack", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("private-key", "private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]

_GROUP_TO_FAMILY = {group: family for family, group, _ in _PATTERNS}
_LINE = re.compile("|".join(f"(?P<{group}>{p.pattern})" for _, group, p in _PATTERNS))
_ALLOW_PRAGMA = "# pragma: allowlist-secret"
_CLASSES = (re.compile(r"[a-z]"), re.compile(r"[A-Z]"), re.compile(r"[0-9]"))


def _mixes_classes(token: str, n: int = 2) -> bool:
    """A real secret's RANDOM PART mixes >= 2 character classes.

    Evaluated on the longest dash-separated segment, so deployment prefixes
    like ``dev-`` (all lowercase) cannot launder a single-class body: the
    placeholder ``tvly-dev-AAAA...`` stays one class, while a real key's
    random tail (``<REDACTED>...``) mixes lower/upper/digit.
    """
    core = max(token.split("-"), key=len)
    return sum(1 for cls in _CLASSES if cls.search(core)) >= n


def _match_is_real(group: str, m: re.Match[str]) -> bool:
    family = _GROUP_TO_FAMILY[group]
    if family == "private-key":
        return True  # an armored private-key header is never a placeholder
    return _mixes_classes(m.group(group))


def scan_lines(lines: list[str], allow_pragma: bool = False) -> list[dict[str, object]]:
    """Return [{family, line_no, snippet}] for real matches, in order."""
    hits: list[dict[str, object]] = []
    for i, raw in enumerate(lines, start=1):
        line = raw.rstrip("\n")
        if allow_pragma and _ALLOW_PRAGMA in line:
            continue
        for m in _LINE.finditer(line):
            group = m.lastgroup or ""
            if group and _match_is_real(group, m):
                hits.append(
                    {
                        "family": _GROUP_TO_FAMILY[group],
                        "line": i,
                        "snippet": line.strip()[:160],
                    }
                )
                break  # one hit per line keeps output readable
    return hits


def _git(*args: str) -> list[str]:
    out = subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True, cwd=Path.cwd()
    )
    return out.stdout.splitlines()


def staged_lines() -> list[str]:
    """Added lines in the staged diff (the pre-commit gate's input)."""
    out = subprocess.run(
        ["git", "diff", "--cached", "-U0"],
        capture_output=True,
        text=True,
        check=True,
        cwd=Path.cwd(),
    )
    added: list[str] = []
    for line in out.stdout.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added.append(line[1:])
    return added


def tree_lines() -> list[str]:
    """Every tracked file's content (the CI / full-tree gate's input)."""
    files = _git("ls-files")
    lines: list[str] = []
    for rel in files:
        p = Path.cwd() / rel
        try:
            if p.is_file():
                lines.extend(p.read_text(encoding="utf-8", errors="replace").splitlines())
        except OSError:  # pragma: no cover - defensive
            continue
    return lines


def diff_lines(base: str) -> list[str]:
    """Added lines between the merge-base of BASE..HEAD and HEAD."""
    base_sha = _git("merge-base", base, "HEAD")
    out = subprocess.run(
        ["git", "diff", "-U0", base_sha[0], "HEAD"],
        capture_output=True,
        text=True,
        check=True,
        cwd=Path.cwd(),
    )
    return [line[1:] for line in out.stdout.splitlines() if line.startswith("+") and not line.startswith("+++")]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--staged", action="store_true", help="scan staged added lines (pre-commit)")
    g.add_argument("--tree", action="store_true", help="scan all tracked file contents (CI)")
    g.add_argument("--diff", metavar="BASE", help="scan added lines vs BASE (branch CI)")
    ap.add_argument("--allow-pragma", action="store_true", help="honor inline allowlist markers")
    args = ap.parse_args(argv)

    if os.environ.get("MSB_SECRET_SCAN_OFF"):
        print("[scan-secrets] MSB_SECRET_SCAN_OFF=1 — scan bypassed by explicit env override")
        return 0

    if args.staged:
        lines = staged_lines()
    elif args.tree:
        lines = tree_lines()
    else:  # --diff
        lines = diff_lines(args.diff)

    hits = scan_lines(lines, allow_pragma=args.allow_pragma)
    if not hits:
        print(f"[scan-secrets] clean — {len(lines)} lines scanned")
        return 0

    print(f"[scan-secrets] BLOCKED — {len(hits)} secret-shaped line(s) found:", file=sys.stderr)
    for h in hits:
        print(
            f"  line {h['line']} [{h['family']}]: {h['snippet']}",
            file=sys.stderr,
        )
    print(
        "[scan-secrets] revoke the key, remove the content, then re-scan. "
        "Inline override: append '# pragma: allowlist-secret'. "
        "Explicit bypass: MSB_SECRET_SCAN_OFF=1 (never default).",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())