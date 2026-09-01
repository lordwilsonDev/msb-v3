"""The Wrongness Engine — run claims through passes + checks, score the corpus.

Three entry points:

- ``run_claim(claim, repo_root)`` — run a live claim: execute its attached
  deterministic checks, run all passes, apply the escalation policy, and
  return the verdict + findings.
- ``run_replay(corpus)`` — reproduce the by-hand 21-decision experiment on
  the machine corpus; returns PEDR / FP-rate scores per SPEC §VII.
- ``run_score(...)`` — the §VII decision rule (VALIDATED / REJECT / MODIFY).

Inversion-audit closures (``03_Inversion-Audit.md``):

- M1: the self-claim (``claims/self_claim.json``) runs the engine on its
  own verdict via the ``corpus_replay`` check.
- M2/M5: a claim carries several checks; mixed outcomes plus recorded
  supporting evidence produce a CONFLICTING verdict, so forward mode can
  return a sharp non-neutral verdict.
- M3: ``use_recorded_routing=False`` replays without the by-hand routing
  annotations (the leakage source), and ``split_held_out`` scores
  deterministic halves for stability.
- M4: ``urgency`` = severity x consequence surfaces on every ClaimResult.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .checks import CheckResult
from .claims import Claim, Finding
from .passes import checks_for_claim, run_all_passes
from .policy import CHECK, CONFLICTING, ESCALATE, claim_verdict, urgency_score


@dataclass(frozen=True)
class ClaimResult:
    claim: Claim
    verdict: str
    findings: list[Finding] = field(default_factory=list)
    checks: tuple[CheckResult, ...] = ()
    urgency: float = 0.0  # severity x consequence (M4)


@dataclass(frozen=True)
class ScoreResult:
    """§VII scoring of a corpus replay."""

    pedr: float  # predicted failures / actual failures
    fp_rate_strict: float  # every flagged finding counts
    fp_rate_assertion: float  # only failure-assertions (ESCALATE) count
    actual_failures: int
    predicted_failures: int
    false_positives_strict: int
    false_positives_assertion: int
    decision: str  # VALIDATED / REJECT / MODIFY
    blind: bool = False  # True when recorded routing was disabled (M3)

    def to_dict(self) -> dict[str, object]:
        return {
            "pedr": self.pedr,
            "fp_rate_strict": self.fp_rate_strict,
            "fp_rate_assertion": self.fp_rate_assertion,
            "actual_failures": self.actual_failures,
            "predicted_failures": self.predicted_failures,
            "false_positives_strict": self.false_positives_strict,
            "false_positives_assertion": self.false_positives_assertion,
            "decision": self.decision,
            "blind": self.blind,
        }


class WrongnessEngine:
    """Deterministic claim-attack engine (MVP)."""

    def __init__(self, repo_root: str | Path) -> None:
        self.repo_root = Path(repo_root)

    def run(self, claim: Claim) -> ClaimResult:
        checks = tuple(checks_for_claim(claim, str(self.repo_root)))
        findings = run_all_passes(claim, list(checks))
        return ClaimResult(
            claim=claim,
            verdict=claim_verdict(findings),
            findings=findings,
            checks=checks,
            urgency=urgency_score(findings, claim.consequence),
        )


def run_claim(claim: Claim, repo_root: str | Path) -> ClaimResult:
    return WrongnessEngine(repo_root).run(claim)


def run_replay(
    corpus: list[Claim],
    repo_root: str | Path | None = None,
    use_recorded_routing: bool = True,
) -> ScoreResult:
    """Replay the by-hand corpus: reproduce PEDR / FP under both semantics.

    Scoring follows SPEC §VII exactly:
    - PEDR = predicted failures that occurred / actual failures
    - FP rate (strict) = every flagged finding that didn't occur
    - FP rate (assertion) = only ESCALATE-tier findings that didn't occur

    ``repo_root`` is optional: when given, the corpus claims' attached
    deterministic checks (C4/C5/C6) are executed live against that tree.

    ``use_recorded_routing=False`` (M3) disables the by-hand escalation
    pins: verdicts come purely from the deterministic machinery, so the
    replay shows what the engine discovers on its own.  CONFLICTING counts
    as a flag (predicted / strict-FP) but not as an assertion-FP.
    """
    actual = 0
    predicted = 0
    fp_strict = 0
    fp_assertion = 0

    for claim in corpus:
        # Only corpus claims with a recorded outcome participate.
        if claim.outcome is None:
            continue
        check_results = checks_for_claim(claim, str(repo_root)) if repo_root is not None else None
        findings = run_all_passes(claim, check_results, use_recorded_routing=use_recorded_routing)
        verdict = claim_verdict(findings)
        flagged = verdict in (CHECK, ESCALATE, CONFLICTING)
        was_failure = claim.outcome == "HIT"
        if was_failure:
            actual += claim.hit_weight
            if flagged:
                predicted += claim.hit_weight
        elif claim.outcome == "FP":
            if flagged:
                fp_strict += 1
            if verdict == ESCALATE:
                fp_assertion += 1
        # CORRECT / "—" rows: neither a failure nor a false positive.

    pedr = predicted / actual if actual else 0.0
    fp_strict_rate = fp_strict / (fp_strict + actual) if (fp_strict + actual) else 0.0
    fp_assertion_rate = fp_assertion / (fp_assertion + actual) if (fp_assertion + actual) else 0.0

    if pedr < 0.3:
        decision = "REJECT"
    elif pedr > 0.5 and fp_strict_rate > 0.5:
        decision = "MODIFY"
    elif pedr > 0.5 and fp_assertion_rate < 0.3:
        decision = "VALIDATED"
    else:
        decision = "MODIFY"

    return ScoreResult(
        pedr=pedr,
        fp_rate_strict=fp_strict_rate,
        fp_rate_assertion=fp_assertion_rate,
        actual_failures=actual,
        predicted_failures=predicted,
        false_positives_strict=fp_strict,
        false_positives_assertion=fp_assertion,
        decision=decision,
        blind=not use_recorded_routing,
    )


def run_score(corpus: list[Claim], repo_root: str | Path) -> ScoreResult:
    """Score a corpus of claims (live or replay) with the §VII rule."""
    return run_replay(corpus)


def split_held_out(corpus: list[Claim], folds: int = 2) -> list[list[Claim]]:
    """Deterministic interleaved split for held-out stability (M3).

    No shuffle: the corpus order is fixed (A1..D2), so the halves are
    reproducible across runs.  Each fold is scored independently; the
    decision must survive on both halves.
    """
    return [[c for i, c in enumerate(corpus) if i % folds == k] for k in range(folds)]


def load_corpus(path: str | Path) -> list[Claim]:
    """Load a JSON corpus file (list of claim dicts)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    claims = data if isinstance(data, list) else data.get("claims", [])
    return [Claim.from_dict(d) for d in claims]


def save_result(result: ClaimResult, path: str | Path) -> None:
    out = {
        "claim": result.claim.to_dict(),
        "verdict": result.verdict,
        "urgency": result.urgency,
        "findings": [
            {
                "pass": f.pass_name,
                "tier": f.tier,
                "statement": f.statement,
                "evidence": f.evidence,
            }
            for f in result.findings
        ],
        "checks": [
            {
                "ok": c.ok,
                "evidence": c.evidence,
                "check": c.check,
                "links": [
                    {"path": link.path, "line": link.line, "snippet": link.snippet}
                    for link in c.links
                ],
            }
            for c in result.checks
        ],
    }
    Path(path).write_text(json.dumps(out, indent=2), encoding="utf-8")
