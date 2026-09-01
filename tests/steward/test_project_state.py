"""Canonical project-state validator tests (steward Layer 02).

Covers the blueprint's hard rules:
  R1  required sections + shapes
  R2  exactly the nine §53 health axes
  R3  health values in GREEN/YELLOW/RED/UNKNOWN
  R4  UNKNOWN != GREEN — a GREEN axis without evidence is invalid
  R5  list-shaped registers present

Hermetic: builds the document in-memory, no filesystem, no repo state.
"""

from __future__ import annotations

from msb_v3.steward import HealthValue
from msb_v3.steward.state import (
    HEALTH_AXES,
    SCHEMA_VERSION,
    ProjectState,
    validate_state,
)

GREEN_EVIDENCE = "full suite 3152 passed (2026-09-01); mypy clean"


def _base() -> dict:
    return {
        "schema": SCHEMA_VERSION,
        "project": {
            "identity": "test-project",
            "mission": "survive",
            "phase": "Year 1",
            "architecture_version": "v0.1.0",
        },
        "objectives": {
            "current": ["one"],
            "quarterly": ["two"],
            "annual": ["three"],
            "five_year": ["four"],
        },
        "constraints": {
            "hardware": "M1",
            "compute": "x",
            "memory": "y",
            "storage": "z",
            "energy": "w",
            "time": "5y",
            "money": "0",
        },
        "health": {axis: "UNKNOWN" for axis in HEALTH_AXES},
        "health_evidence": {},
        "execution": {
            "active": ["a"],
            "blocked": ["b"],
            "completed": ["c"],
            "abandoned": [],
        },
        "unknowns": ["what am I missing?"],
        "contradictions": ["a vs b"],
        "risks": ["something"],
        "opportunities": ["something else"],
        "updated": "2026-09-01",
    }


def test_valid_state_passes(green_all: bool = False) -> None:
    state = _base()
    if green_all:
        state["health"] = {axis: "GREEN" for axis in HEALTH_AXES}
        state["health_evidence"] = {axis: GREEN_EVIDENCE for axis in HEALTH_AXES}
    result = validate_state(state)
    assert result.valid, [str(i) for i in result.issues]


def test_green_all_with_evidence_valid() -> None:
    test_valid_state_passes(green_all=True)


def test_invalid_schema_version_rejected() -> None:
    state = _base()
    state["schema"] = "wrong/v1"
    result = validate_state(state)
    assert not result.valid
    assert any(i.path == "schema" for i in result.issues)


def test_missing_mission_rejected() -> None:
    state = _base()
    del state["project"]["mission"]
    result = validate_state(state)
    assert not result.valid
    assert any(i.path == "project.mission" for i in result.issues)


def test_unknown_is_valid_health_value() -> None:
    state = _base()
    state["health"]["TECHNICAL"] = "UNKNOWN"
    result = validate_state(state)
    assert result.valid


def test_green_without_evidence_is_the_core_rule() -> None:
    """R4 — UNKNOWN != GREEN: green must be earned, not assumed."""
    state = _base()
    state["health"] = {axis: "GREEN" for axis in HEALTH_AXES}
    # health_evidence left empty on purpose
    result = validate_state(state)
    assert not result.valid
    green_no_evidence = [i.path for i in result.issues if "GREEN" in i.message]
    assert len(green_no_evidence) == len(HEALTH_AXES)


def test_invented_health_axis_rejected() -> None:
    state = _base()
    state["health"]["MOOD"] = "GREEN"
    result = validate_state(state)
    assert not result.valid
    assert any(i.path == "health.MOOD" for i in result.issues)


def test_bad_health_value_rejected() -> None:
    state = _base()
    state["health"]["TECHNICAL"] = "MAYBE_OKAY"
    result = validate_state(state)
    assert not result.valid
    assert any(i.path == "health.TECHNICAL" for i in result.issues)


def test_missing_register_rejected() -> None:
    state = _base()
    del state["risks"]
    result = validate_state(state)
    assert not result.valid
    assert any(i.path == "risks" for i in result.issues)


def test_risks_must_be_list_of_strings() -> None:
    state = _base()
    state["risks"] = [{"structured": True}]
    result = validate_state(state)
    assert not result.valid
    assert any(i.path == "risks" for i in result.issues)


def test_missing_execution_bucket_rejected() -> None:
    state = _base()
    del state["execution"]["abandoned"]
    result = validate_state(state)
    assert not result.valid
    assert any(i.path == "abandoned" for i in result.issues)


def test_missing_updated_rejected() -> None:
    state = _base()
    del state["updated"]
    result = validate_state(state)
    assert not result.valid
    assert any(i.path == "updated" for i in result.issues)


def test_health_table_renders_all_axes() -> None:
    state = _base()
    result = validate_state(state)
    table = result.health_table()
    for axis in HEALTH_AXES:
        assert axis in table


def test_load_state_bad_json(tmp_path) -> None:
    from msb_v3.steward.state import load_state

    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    result = load_state(bad)
    assert not result.valid
    assert any("invalid JSON" in i.message for i in result.issues)


def test_health_value_enum_values() -> None:
    assert {v.value for v in HealthValue} == {"GREEN", "YELLOW", "RED", "UNKNOWN"}


def test_project_state_defaults_to_invalid() -> None:
    assert not ProjectState().valid