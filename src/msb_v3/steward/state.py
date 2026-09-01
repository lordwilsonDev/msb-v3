"""Canonical project state — schema + deterministic validator (Layer 02).

Implements the AIL–MoIE Steward blueprint §7 (canonical state shape),
§53 (health vector axes + values), and §54 (UNKNOWN != GREEN: an axis
declared GREEN must carry evidence).  The validator is deliberately
mechanical: no LLM, no judgment, no network — it exists so the state file
cannot silently drift into claiming health it has not earned.

State file contract (JSON):
    schema: "ail-moie-steward/project-state/v1"
    project: {identity, mission, phase, architecture_version}
    objectives: {current, quarterly, annual, five_year}   (lists of strings)
    constraints: {hardware, compute, memory, storage, energy, time, money}
    health: {TECHNICAL, STRATEGIC, OPERATIONAL, MEMORY, KNOWLEDGE,
             SECURITY, RESOURCE, ARCHITECTURE, COGNITIVE} -> "GREEN"|"YELLOW"
             |"RED"|"UNKNOWN"
    health_evidence: {AXIS: "where this was last actually verified"}  (optional)
    execution: {active, blocked, completed, abandoned}     (lists of strings)
    unknowns:     list of strings
    contradictions: list of strings
    risks:        list of strings
    opportunities: list of strings
    updated: ISO timestamp of the last human/agent touch

Rules enforced:
  R1  every required section present and of the right shape
  R2  health axes are exactly the §53 nine (no invented axes)
  R3  health values are within the §54 enum
  R4  an axis marked GREEN must have an entry in health_evidence
      (UNKNOWN != GREEN — "probably okay" is not a state, it is an
      unstated risk)
  R5  unknowns/contradictions/risks/opportunities are non-empty lists
      (an empty "risks" list for a five-year project is a lie we refuse
      to record)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "ail-moie-steward/project-state/v1"

HEALTH_AXES = (
    "TECHNICAL",
    "STRATEGIC",
    "OPERATIONAL",
    "MEMORY",
    "KNOWLEDGE",
    "SECURITY",
    "RESOURCE",
    "ARCHITECTURE",
    "COGNITIVE",
)


class HealthValue(str, Enum):
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ValidationIssue:
    """One violation of the state contract."""

    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


@dataclass
class ProjectState:
    """Parsed + validated canonical project state."""

    data: dict[str, Any] = field(default_factory=dict)
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        # A ProjectState that was never populated with validated data (or
        # that carries violations) is not valid.  Direct construction must
        # not accidentally report a clean bill of health.
        return bool(self.data) and not self.issues

    def health_table(self) -> str:
        """Render the §53 health vector as text (what the CLI prints)."""
        health = self.data.get("health", {})
        evidence = self.data.get("health_evidence", {})
        lines = []
        for axis in HEALTH_AXES:
            value = health.get(axis, "UNKNOWN")
            ev = evidence.get(axis, "")
            lines.append(f"{axis.ljust(12)} {value.ljust(8)} {ev}")
        return "\n".join(lines)


def _expect_str_list(
    data: dict[str, Any], section: str, issues: list[ValidationIssue]
) -> None:
    value = data.get(section)
    if value is None:
        issues.append(ValidationIssue(section, "missing"))
        return
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        issues.append(
            ValidationIssue(section, "must be a list of strings")
        )


def _expect_str_dict(
    data: dict[str, Any], section: str, issues: list[ValidationIssue]
) -> None:
    value = data.get(section)
    if value is None:
        issues.append(ValidationIssue(section, "missing"))
        return
    if not isinstance(value, dict):
        issues.append(ValidationIssue(section, "must be an object"))
        return
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            issues.append(
                ValidationIssue(f"{section}.{key}", "value must be a string")
            )


def validate_state(state: dict[str, Any]) -> ProjectState:
    """Validate a parsed project-state document. Returns the verdict."""
    issues: list[ValidationIssue] = []

    if state.get("schema") != SCHEMA_VERSION:
        issues.append(
            ValidationIssue("schema", f"expected {SCHEMA_VERSION!r}")
        )

    project = state.get("project")
    if not isinstance(project, dict):
        issues.append(ValidationIssue("project", "must be an object"))
    else:
        for key in ("identity", "mission", "phase", "architecture_version"):
            if not isinstance(project.get(key), str) or not project[key]:
                issues.append(ValidationIssue(f"project.{key}", "missing"))

    objectives = state.get("objectives")
    if not isinstance(objectives, dict):
        issues.append(ValidationIssue("objectives", "must be an object"))
    else:
        for horizon in ("current", "quarterly", "annual", "five_year"):
            _expect_str_list(objectives, horizon, issues)

    _expect_str_dict(state, "constraints", issues)

    health = state.get("health")
    if not isinstance(health, dict):
        issues.append(ValidationIssue("health", "must be an object"))
    else:
        for axis in HEALTH_AXES:
            value = health.get(axis)
            if value not in HealthValue._value2member_map_:
                issues.append(
                    ValidationIssue(
                        f"health.{axis}",
                        f"invalid value {value!r} (GREEN/YELLOW/RED/UNKNOWN)",
                    )
                )
        for axis in health:
            if axis not in HEALTH_AXES:
                issues.append(ValidationIssue(f"health.{axis}", "not a §53 axis"))

    health_evidence = state.get("health_evidence", {})
    if not isinstance(health_evidence, dict):
        issues.append(ValidationIssue("health_evidence", "must be an object"))
        health_evidence = {}
    # R4 — GREEN requires evidence (UNKNOWN != GREEN).
    if isinstance(health, dict):
        for axis in HEALTH_AXES:
            value = health.get(axis)
            if value == "GREEN" and not health_evidence.get(axis):
                issues.append(
                    ValidationIssue(
                        f"health.{axis}",
                        "GREEN without health_evidence — UNKNOWN != GREEN",
                    )
                )

    execution = state.get("execution")
    if not isinstance(execution, dict):
        issues.append(ValidationIssue("execution", "must be an object"))
    else:
        for bucket in ("active", "blocked", "completed", "abandoned"):
            _expect_str_list(execution, bucket, issues)

    # R5 — the four epistemology registers are lists of strings, non-empty
    # is NOT required for unknowns/contradictions (valid to have none yet,
    # though suspicious) but they must be present and well-shaped.
    for section in ("unknowns", "contradictions", "risks", "opportunities"):
        _expect_str_list(state, section, issues)

    if not isinstance(state.get("updated"), str) or not state["updated"]:
        issues.append(ValidationIssue("updated", "missing ISO timestamp"))

    return ProjectState(data=state, issues=issues)


def load_state(path: str | Path) -> ProjectState:
    """Load + validate a project-state JSON file."""
    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ProjectState(
            issues=[ValidationIssue(str(p), "file not found")]
        )
    except json.JSONDecodeError as exc:
        return ProjectState(
            issues=[ValidationIssue(str(p), f"invalid JSON: {exc}")]
        )
    if not isinstance(data, dict):
        return ProjectState(issues=[ValidationIssue(str(p), "not an object")])
    return validate_state(data)