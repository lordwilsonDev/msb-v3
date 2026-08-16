"""Software Factory classifier (spec §4.2.6 — classify stage).

Deterministic: issue type and severity come from the issue's own
language; scope is a best-effort code-graph lookup of the symbols the
issue names (a failure degrades to an empty scope — honest, never
invented).
"""

from __future__ import annotations

from typing import Any, List, Optional

from msb_v3.factory.models import Classification, Issue
from msb_v3.moie.pipeline import keyword_hits

_TYPE_KEYWORDS = {
    "bug": ("bug", "fix", "crash", "error", "broken", "fails", "exception", "regression", "incorrect"),
    "feature": ("add", "feature", "support", "implement", "introduce", "new", "allow", "enable"),
    "refactor": ("refactor", "cleanup", "restructure", "simplify", "rename", "deduplicate", "tech debt"),
    "security": ("security", "auth", "injection", "vulnerability", "cve", "credential", "permission", "xss", "ssrf"),
}

_SEVERITY_KEYWORDS = {
    "critical": ("critical", "p0", "data loss", "breach", "exploit", "crash on production"),
    "high": ("high", "p1", "blocker", "downtime", "security"),
    "medium": ("medium", "p2", "significant", "regression"),
    "low": ("low", "p3", "minor", "cosmetic", "nit", "typo"),
}

_TYPE_ORDER = ("security", "bug", "feature", "refactor")  # security wins ties


def classify(issue: Issue, *, codegraph: Any = None, repo: Optional[str] = None) -> Classification:
    text = f"{issue.title} {issue.body}".lower()

    issue_type = "other"
    best: List[str] = []
    for t, kws in _TYPE_KEYWORDS.items():
        hits = keyword_hits(text, kws)
        if hits and len(hits) > len(best):
            best = hits
            issue_type = t
    for t in _TYPE_ORDER:  # security beats bug beats feature beats refactor
        if keyword_hits(text, _TYPE_KEYWORDS[t]):
            issue_type = t
            break

    severity = "medium"
    for sev, kws in (("critical", _SEVERITY_KEYWORDS["critical"]), ("high", _SEVERITY_KEYWORDS["high"]), ("medium", _SEVERITY_KEYWORDS["medium"]), ("low", _SEVERITY_KEYWORDS["low"])):
        if keyword_hits(text, kws):
            severity = sev
            break
    for label in issue.labels:
        label_low = label.lower()
        for sev, kws in _SEVERITY_KEYWORDS.items():
            if label_low in kws or label_low == sev:
                severity = sev

    scope: List[str] = []
    if codegraph is not None and repo:
        try:
            symbols = _extract_symbols(text)
            seen: set[str] = set()
            for sym in symbols[:5]:
                for hit in codegraph.find_symbol(repo, sym, limit=2):
                    if hit["fq_name"] in seen:
                        continue
                    seen.add(hit["fq_name"])
                    scope.append(f"{hit['file']}:{hit['line']} {hit['fq_name']}")
        except Exception:  # noqa: BLE001 — scope is best-effort
            scope = []

    rationale = (
        f"type={issue_type} (keyword: {best[0] if best else 'none'}), severity={severity}, "
        f"scope={len(scope)} code-graph hit(s)"
    )
    return Classification(issue_type=issue_type, severity=severity, scope=scope[:10], rationale=rationale)


def _extract_symbols(text: str) -> List[str]:
    """Symbol-ish tokens from the issue text (best-effort)."""
    import re

    # CamelCase / snake_case / dotted identifiers of length >= 3.
    candidates = re.findall(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", text)
    return [c for c in candidates if len(c) >= 3][:8]
