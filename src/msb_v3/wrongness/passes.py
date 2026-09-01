"""The seven adversarial passes (SPEC §VII / doc §X).

Each pass is a deterministic template: given a claim, it generates a
falsification finding.  The engine's job is to *systematize checks that
exist but are skipped under ordinary acceptance* — so these templates are
mostly routing: they inspect the claim's falsification conditions, run the
deterministic checks when attached, and assign a tier per the escalation
policy (investigation-prompts -> CHECK, never ESCALATE on their own).

Pass ranking from the by-hand run (hits): Boundary > Counterexample >
Incentive > Scaling / Failure-cascade / Assumption / Attack.

Two additions from the inversion audit:

- ``evidence`` — not an adversarial pass but a confirming signal: recorded
  supporting evidence emits a NOTE finding so a refuting check can produce
  a CONFLICTING verdict (M2) instead of collapsing to max tier.
- ``use_recorded_routing`` — corpus replay normally pins the by-hand
  ``strongest_pass`` tier to the recorded escalation class.  Blind mode
  (False) disables the pin so the replay shows what the machinery itself
  discovers without the author's routing annotations (M3).
"""

from __future__ import annotations

from pathlib import Path

from .checks import CheckResult, run_check
from .claims import Claim, Finding
from .policy import CHECK, ESCALATE, NOTE, tier_for_class

PASS_NAMES = (
    "attack",
    "counterexample",
    "assumption",
    "boundary",
    "incentive",
    "scaling",
    "failure_cascade",
)


def _finding(pass_name: str, statement: str, tier: str = CHECK, evidence: str | None = None) -> Finding:
    return Finding(pass_name=pass_name, tier=tier, statement=statement, evidence=evidence)


def pass_evidence(claim: Claim) -> list[Finding]:
    """Recorded supporting evidence — a confirming signal (M2).

    Not an adversarial pass: it lets the engine represent "the evidence
    conflicts" (B1: the closer report says CLOSED, the 83% reset refutes).
    NOTE tier, so it never escalates on its own and never blocks.
    """
    if not claim.supporting_evidence:
        return []
    return [
        _finding(
            "evidence",
            f"Recorded supporting evidence stands: {'; '.join(claim.supporting_evidence)} — "
            "a refuting signal must outweigh it (CONFLICTING if both hold).",
            tier=NOTE,
            evidence="; ".join(claim.supporting_evidence),
        )
    ]


def pass_attack(claim: Claim) -> list[Finding]:
    """Attack: what is the strongest counterargument / who is the authority?"""
    return [
        _finding(
            "attack",
            f"Strongest counterargument to '{claim.statement}': "
            f"{claim.falsification_conditions[0] if claim.falsification_conditions else 'state the null hypothesis explicitly'}.",
        )
    ]


def pass_counterexample(claim: Claim) -> list[Finding]:
    """Counterexample: construct the input that breaks the claim."""
    cond = claim.falsification_conditions[0] if claim.falsification_conditions else "a concrete counterexample"
    return [
        _finding(
            "counterexample",
            f"Construct a counterexample: {cond}. If it cannot be constructed, the claim is unfalsifiable.",
        )
    ]


def pass_assumption(claim: Claim) -> list[Finding]:
    """Assumption: which implicit assumptions is the claim standing on?"""
    return [
        _finding(
            "assumption",
            "Enumerate the implicit assumptions (e.g. 'installed == working', 'n>0', 'same environment'). "
            "Each unstated assumption is a falsification gap.",
        )
    ]


def pass_boundary(claim: Claim, check_results: list[CheckResult] | None = None) -> list[Finding]:
    """Boundary: does the check's coverage set match the claim's scope?

    The by-hand run's top pass.  Each attached deterministic check decides
    its own tier: a failed check is an evidence-backed failure-assertion ->
    ESCALATE; a passed check confirms -> NOTE; inconclusive -> CHECK.  With
    several checks, mixed outcomes are the CONFLICTING signal (M5: the fleet
    claim's code gate passes while its automation gate fails).
    """
    results = check_results or []
    if not results:
        return [
            _finding(
                "boundary",
                "Boundary audit: does the evidence path-set cover every tracked path the claim touches "
                "(porcelain state, stat mode, call-site count, tracked-path coverage)?",
            )
        ]
    findings = []
    for res in results:
        if res.ok is False:
            tier = ESCALATE
            verb = "FAILED (refuting signal)"
        elif res.ok is True:
            tier = NOTE
            verb = "passed (confirming signal)"
        else:
            tier = CHECK
            verb = "inconclusive"
        findings.append(
            _finding("boundary", f"Deterministic boundary check {verb}: {res.evidence}", tier=tier, evidence=res.check)
        )
    return findings


def pass_incentive(claim: Claim) -> list[Finding]:
    """Incentive: who benefits from this claim being true?"""
    return [
        _finding(
            "incentive",
            "Who/what grows when this claim is accepted? Verify the number, not the narrative "
            "(closer '100% CLOSED' was 83% on the next run).",
        )
    ]


def pass_scaling(claim: Claim) -> list[Finding]:
    """Scaling: does it hold at n=1, n=50, n=10k?"""
    return [
        _finding(
            "scaling",
            "Scaling probe: the claim holds at n=? — verify at a larger n than the proof used "
            "(n=1 'verification' was vacuous in the RAG layer).",
        )
    ]


def pass_failure_cascade(claim: Claim) -> list[Finding]:
    """Failure-cascade: what breaks on restart / partial failure?"""
    return [
        _finding(
            "failure_cascade",
            "Failure-cascade probe: what happens on restart, lock contention, or partial failure "
            "(stop/start race on Qdrant's WAL was a real hazard)?",
        )
    ]


def run_all_passes(
    claim: Claim,
    check_results: list[CheckResult] | None = None,
    use_recorded_routing: bool = True,
) -> list[Finding]:
    """Run the full pass suite over a claim (used by the engine and replay)."""
    findings: list[Finding] = []
    findings.extend(pass_evidence(claim))
    findings.extend(pass_attack(claim))
    findings.extend(pass_counterexample(claim))
    findings.extend(pass_assumption(claim))
    findings.extend(pass_boundary(claim, check_results))
    findings.extend(pass_incentive(claim))
    findings.extend(pass_scaling(claim))
    findings.extend(pass_failure_cascade(claim))
    # Corpus replay: when the by-hand run recorded a strongest pass with an
    # escalation class, pin that pass's tier to the recorded routing.  Only
    # the adversarial passes can be pinned — the ``evidence`` pass is a
    # confirming signal and must survive intact so CONFLICTING is reachable.
    # Blind mode (M3) disables the pin entirely.
    if use_recorded_routing and claim.strongest_pass and claim.escalation_class:
        tier = tier_for_class(claim.escalation_class)
        findings = [
            Finding(
                pass_name=f.pass_name,
                tier=tier if f.pass_name == claim.strongest_pass else f.tier,
                statement=f.statement,
                evidence=f.evidence,
            )
            for f in findings
        ]
    return findings


def checks_for_claim(claim: Claim, repo_root: str) -> list[CheckResult]:
    """Run every attached deterministic check against ``repo_root``."""
    return [run_check(spec, Path(repo_root)) for spec in claim.checks]
