"""The single reasoning call (doc 4 §6).

v1 substrate is the ``claude`` CLI in headless JSON mode. The model may only
return ``NO_ACTION`` / ``PROPOSE`` / ``ESCALATE`` — never ``ALLOW`` — and has
no execution tools. Output is validated against :class:`GuardianResult`; on
failure we retry ``max_retries`` times, then fall back to a deterministic
``ESCALATE`` (never fabricate a clean result).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from .config import GuardianConfig

Decision = Literal["NO_ACTION", "PROPOSE", "ESCALATE"]

_OBSERVE_RIDER = (
    "\n\n--- RUNTIME RIDER (v1) ---\n"
    "You are in OBSERVE mode. You may return decision NO_ACTION, PROPOSE, or "
    "ESCALATE. You may NOT return ALLOW. You have no execution tools and must "
    "not describe having performed any mutation. Every finding must carry a "
    "claim_type (FACT|INFERENCE|HYPOTHESIS) and an evidence_ref that points at "
    "a field in the forensics bundle or a repo path. You may only cite test "
    "counts that appear in the bundle; anything the bundle marks NOT_RUN stays "
    "NOT_RUN. Respond with a single JSON object and nothing else."
)


class Finding(BaseModel):
    cls: str = Field(alias="class")
    severity: Literal["info", "low", "medium", "high", "critical"]
    claim_type: Literal["FACT", "INFERENCE", "HYPOTHESIS"]
    evidence_ref: str
    statement: str
    recommended_action: str = ""
    authorization_needed: Literal["human", "policy", "none"] = "none"

    model_config = {"populate_by_name": True}


class Proposal(BaseModel):
    intent_id: str
    change_class: Literal["LOW_RISK", "MEDIUM_RISK", "HIGH_RISK"]
    objective: str
    target_files: list[str] = Field(default_factory=list)
    required_tests: list[str] = Field(default_factory=list)
    rollback_strategy: str = ""
    why_not_auto: str = "OBSERVE mode"


class Escalation(BaseModel):
    reason: str
    blocking: bool = True
    evidence_ref: str = ""
    detail: str = ""


class GuardianResult(BaseModel):
    run_id: str
    mission: str = "repository_stewardship"
    decision: Decision
    summary: str = ""
    findings: list[Finding] = Field(default_factory=list)
    proposals: list[Proposal] = Field(default_factory=list)
    escalations: list[Escalation] = Field(default_factory=list)
    controls_passed: list[str] = Field(default_factory=list)
    tests: dict[str, object] = Field(default_factory=dict)
    reasoning_tokens: int | None = None


def _system_prompt(cfg: GuardianConfig) -> str:
    try:
        base = cfg.reasoning.system_prompt_path.read_text(encoding="utf-8")
    except OSError:
        base = "S-AOS Autonomous Guardian. Governing spec unreadable; be conservative."
    return base + _OBSERVE_RIDER


def _deterministic_escalation(run_id: str, reason: str, detail: str) -> GuardianResult:
    return GuardianResult(
        run_id=run_id,
        decision="ESCALATE",
        summary=f"Deterministic fallback: {reason}",
        escalations=[Escalation(reason=reason, blocking=True, detail=detail)],
        controls_passed=["schema_valid", "no_mutation"],
        tests={"passed": 0, "failed": 0, "skipped": 0, "not_run": "all"},
    )


def _invoke_cli(cfg: GuardianConfig, system: str, user: str) -> tuple[str, int | None]:
    binp = shutil.which(cfg.reasoning.claude_bin)
    if binp is None:
        raise FileNotFoundError(cfg.reasoning.claude_bin)
    args = [binp, "-p", user, "--output-format", "json", "--append-system-prompt", system]
    if cfg.reasoning.model:
        args += ["--model", cfg.reasoning.model]
    p = subprocess.run(args, capture_output=True, text=True, timeout=240, check=False)
    if p.returncode != 0:
        raise RuntimeError(f"claude cli exit {p.returncode}: {p.stderr[:500]}")
    envelope = json.loads(p.stdout)
    # `claude -p --output-format json` wraps the reply in {result, usage, ...}
    text = envelope.get("result", p.stdout) if isinstance(envelope, dict) else p.stdout
    usage = envelope.get("usage", {}) if isinstance(envelope, dict) else {}
    tokens = None
    if isinstance(usage, dict):
        it = usage.get("input_tokens")
        ot = usage.get("output_tokens")
        if isinstance(it, int) and isinstance(ot, int):
            tokens = it + ot
    return text, tokens


def _extract_json(text: str) -> dict[str, object]:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in model output")
    parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("model output was not a JSON object")
    return parsed


def classify(cfg: GuardianConfig, run_id: str, forensics: dict[str, object]) -> GuardianResult:
    """Run the reasoning step. Always returns a validated result."""
    if cfg.reasoning.substrate != "claude_cli":
        return _deterministic_escalation(
            run_id, "CAPABILITY_UNAVAILABLE", f"substrate {cfg.reasoning.substrate!r} not wired in v1"
        )

    system = _system_prompt(cfg)
    user = (
        "FORENSICS BUNDLE (the only evidence you may cite):\n"
        + json.dumps(forensics, indent=2)
        + "\n\nReturn a JSON object matching the GuardianResult schema: "
        "run_id, mission, decision, summary, findings[], proposals[], "
        "escalations[], controls_passed[], tests{}."
    )

    last_err = ""
    for _ in range(max(1, cfg.reasoning.max_retries + 1)):
        try:
            text, tokens = _invoke_cli(cfg, system, user)
            data = _extract_json(text)
            data.setdefault("run_id", run_id)
            result = GuardianResult.model_validate(data)
            if result.reasoning_tokens is None:
                result.reasoning_tokens = tokens
            if "no_mutation" not in result.controls_passed:
                result.controls_passed.append("no_mutation")
            return result
        except FileNotFoundError:
            return _deterministic_escalation(
                run_id, "CAPABILITY_UNAVAILABLE", "claude CLI not on PATH"
            )
        except (RuntimeError, ValueError, ValidationError, json.JSONDecodeError) as exc:
            last_err = str(exc)[:500]

    return _deterministic_escalation(
        run_id, "REASONING_UNVALIDATED", f"model output failed validation: {last_err}"
    )
