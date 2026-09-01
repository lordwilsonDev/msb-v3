"""M7: the human read-path for claim runs.

``render_report`` turns a ClaimResult into actionable markdown.  Its job is
the audit's M7 miss: CHECK-tier findings are investigation prompts, and an
investigation prompt that only exists as a JSON blob is as good as never
raised.  The report gives each CHECK finding an explicit investigation
path — the machine-readable evidence links (M6) plus the question to
answer — and gives CONFLICTING verdicts both sides of the evidence so a
human can actually decide.

Nothing here invents evidence: every location comes from
``CheckResult.links`` (the deterministic checks) and every statement from
the passes.  This is presentation, not adjudication.
"""

from __future__ import annotations

from pathlib import Path

from .checks import EvidenceLink
from .engine import ClaimResult
from .policy import CHECK, CONFLICTING, ESCALATE, NOTE, passes_agreeing

_VERDICT_GUIDANCE = {
    ESCALATE: (
        "Blocking — treat as a failure-assertion. Re-run the failing check "
        "yourself, then either fix the finding or formally reject the claim."
    ),
    CONFLICTING: (
        "Evidence points both ways — this is a human decision. Weigh the "
        "confirming and refuting signals below, then either promote the claim "
        "(the refutation is an accepted limitation) or kill it (the refutation "
        "outweighs the supporting evidence)."
    ),
    CHECK: (
        "Investigation prompt — route below escalation. Answer the specific "
        "question in the findings with the linked evidence; do NOT block on it."
    ),
    NOTE: "No adversarial signal. Informational — nothing to act on.",
}

_VERDICT_ICON = {ESCALATE: "⛔", CONFLICTING: "⚖️", CHECK: "🔍", NOTE: "ℹ️"}


def _format_link(link: EvidenceLink, repo_root: str | None) -> str:
    p = link.path
    if repo_root and not Path(p).is_absolute():
        p = str(Path(repo_root) / p)
    if link.line is not None:
        p = f"{p}:{link.line}"
    return p


def render_report(result: ClaimResult, repo_root: str | None = None) -> str:
    """Render a claim run as actionable markdown (M7)."""
    claim = result.claim
    icon = _VERDICT_ICON.get(result.verdict, "•")
    lines: list[str] = [
        f"# Wrongness report — {claim.id}",
        "",
        f"**Statement:** {claim.statement}",
        "",
        f"**Verdict:** {icon} {result.verdict}  "
        f"|  **Urgency:** {result.urgency:.2f} (consequence={claim.consequence})",
    ]
    if claim.domain:
        lines.append(f"**Domain:** {claim.domain}")
    if claim.falsification_conditions:
        lines.append("")
        lines.append("**Falsification conditions:**")
        lines.extend(f"- {c}" for c in claim.falsification_conditions)
    lines.append("")

    if result.checks:
        lines.append("## Deterministic checks")
        lines.append("")
        for c in result.checks:
            status = "PASS" if c.ok is True else ("FAIL" if c.ok is False else "INCONCLUSIVE")
            lines.append(f"- **[{status}]** `{c.check}` — {c.evidence}")
            for link in c.links:
                loc = _format_link(link, repo_root)
                snippet = f" — `{link.snippet}`" if link.snippet else ""
                lines.append(f"  - evidence: `{loc}`{snippet}")
        lines.append("")

    for tier in (ESCALATE, CONFLICTING, CHECK, NOTE):
        tier_findings = [f for f in result.findings if f.tier == tier]
        if not tier_findings:
            continue
        lines.append(f"## {tier} findings ({len(tier_findings)})")
        lines.append("")
        for f in tier_findings:
            evidence = f" — *{f.evidence}*" if f.evidence else ""
            lines.append(f"- **{f.pass_name}:** {f.statement}{evidence}")
        agreeing = passes_agreeing(result.findings, tier)
        if agreeing:
            lines.append("")
            lines.append(
                f"*Consensus: {len(agreeing)} pass(es) agree at {tier}: "
                f"{', '.join(agreeing)}*"
            )
        lines.append("")

    # The M7 core: CHECK findings are routing prompts — give the human the
    # concrete locations (from inconclusive checks) and the question.
    if any(f.tier == CHECK for f in result.findings):
        lines.append("## Investigation path (CHECK findings)")
        lines.append("")
        lines.append(
            "These are routing prompts, not failures — answer the question, "
            "don't block. Where to look:"
        )
        inconclusive = [c for c in result.checks if c.ok is None]
        if inconclusive:
            for c in inconclusive:
                lines.append(f"- `{c.check}`: {c.evidence}")
                for link in c.links:
                    lines.append(f"  - look at `{_format_link(link, repo_root)}`")
        else:
            lines.append(
                "- No attached check is inconclusive — the CHECK findings are "
                "routing prompts for angles not yet mechanized (see pass "
                "statements above)."
            )
        lines.append("")

    lines.append("## Guidance")
    lines.append("")
    lines.append(_VERDICT_GUIDANCE.get(result.verdict, ""))

    if claim.evidence_refs:
        lines.append("")
        lines.append("## Evidence refs")
        lines.extend(f"- {r}" for r in claim.evidence_refs)

    return "\n".join(lines) + "\n"
