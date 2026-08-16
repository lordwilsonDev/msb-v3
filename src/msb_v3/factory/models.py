"""Software Factory data model (spec §4.2.6, §8-10).

A FactoryRun is the complete, self-contained record of one issue processed
end-to-end: plan, build evidence, test evidence, independent review,
grounded verification, and a final verdict — every stage carried with
hashes so a claim ("tests passed") can be re-derived from the evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Issue:
    title: str
    body: str = ""
    repo: str = ""  # repo key / path hint
    labels: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {"title": self.title, "body": self.body, "repo": self.repo, "labels": self.labels}


@dataclass
class Classification:
    issue_type: str = "other"  # bug | feature | refactor | security | other
    severity: str = "medium"  # low | medium | high | critical
    scope: List[str] = field(default_factory=list)  # files/symbols touched (codegraph, best-effort)
    rationale: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {"issue_type": self.issue_type, "severity": self.severity, "scope": self.scope, "rationale": self.rationale}


@dataclass
class PlanStep:
    step_id: str
    title: str
    action: str  # what the builder must do
    acceptance: List[str] = field(default_factory=list)  # verifiable criteria

    def as_dict(self) -> Dict[str, Any]:
        return {"step_id": self.step_id, "title": self.title, "action": self.action, "acceptance": self.acceptance}


@dataclass
class Plan:
    goal: str = ""
    steps: List[PlanStep] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)  # from MoIE inversion
    assumptions: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "goal": self.goal,
            "steps": [s.as_dict() for s in self.steps],
            "risks": self.risks,
            "assumptions": self.assumptions,
        }


@dataclass
class BuildResult:
    ok: bool
    worktree: str = ""
    changed_files: List[str] = field(default_factory=list)
    diff: str = ""  # bounded unified diff of the changes
    output_head: str = ""
    error: Optional[str] = None
    builder: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "changed_files": self.changed_files,
            "diff_head": self.diff[:2000],
            "output_head": self.output_head[:1000],
            "error": self.error,
            "builder": self.builder,
        }


@dataclass
class TestEvidence:
    command: str = ""
    exit_code: Optional[int] = None
    passed: bool = False
    output_head: str = ""
    duration_s: float = 0.0
    ran: bool = False  # False = no test command found — honest UNVERIFIED

    def as_dict(self) -> Dict[str, Any]:
        return {
            "command": self.command,
            "exit_code": self.exit_code,
            "passed": self.passed,
            "output_head": self.output_head[:1000],
            "duration_s": round(self.duration_s, 3),
            "ran": self.ran,
        }


@dataclass
class ReviewFinding:
    severity: str  # info | concern | blocker
    message: str

    def as_dict(self) -> Dict[str, Any]:
        return {"severity": self.severity, "message": self.message}


@dataclass
class Review:
    verdict: str  # APPROVE | CONCERN | BLOCK
    findings: List[ReviewFinding] = field(default_factory=list)
    moie_verdict: str = ""  # the independent MoIE decision's verdict
    moie_ids: float = 0.0
    independent: bool = True  # never the builder's own claim
    reviewer_models: List[str] = field(default_factory=list)  # distinct reviewer models (LLM panel)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict,
            "findings": [f.as_dict() for f in self.findings],
            "moie_verdict": self.moie_verdict,
            "moie_ids": self.moie_ids,
            "independent": self.independent,
            "reviewer_models": self.reviewer_models,
        }


@dataclass
class VerificationCheck:
    criterion: str
    result: str  # PASS | FAIL | UNVERIFIED
    evidence: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {"criterion": self.criterion, "result": self.result, "evidence": self.evidence}


@dataclass
class Verification:
    verdict: str = "UNVERIFIED"  # PASS | FAIL | UNVERIFIED (never PASS without evidence)
    checks: List[VerificationCheck] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {"verdict": self.verdict, "checks": [c.as_dict() for c in self.checks]}


@dataclass
class FactoryRun:
    issue: Issue
    classification: Classification = field(default_factory=Classification)
    plan: Plan = field(default_factory=Plan)
    build: Optional[BuildResult] = None
    test: TestEvidence = field(default_factory=TestEvidence)
    review: Optional[Review] = None
    verification: Verification = field(default_factory=Verification)
    verdict: str = "FAILED"  # MERGED | NEEDS_WORK | BLOCKED | FAILED
    evidence_chain: List[str] = field(default_factory=list)  # stage hashes
    error: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "issue": self.issue.as_dict(),
            "classification": self.classification.as_dict(),
            "plan": self.plan.as_dict(),
            "build": self.build.as_dict() if self.build else None,
            "test": self.test.as_dict(),
            "review": self.review.as_dict() if self.review else None,
            "verification": self.verification.as_dict(),
            "verdict": self.verdict,
            "evidence_chain": self.evidence_chain,
            "error": self.error,
        }
