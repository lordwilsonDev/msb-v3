'''Phase 2 calibration contract — the "magic numbers" made explicit.

Pins MSB-CAL-001 (20260817): MoIE's verdict is a pure function of expert
keyword hits; the numeric constants are confidence-only. Executable
guarantees:

- MSB-CAL-002: the externalized risk templates (config/risk_templates.json)
  are the live policy surface — editing the JSON changes what experts say.
- MSB-CAL-003: the calibrated constants load from config/calibrated.yaml
  (env MSB_CALIBRATION_PATH overrides), clamped to sane bounds on load.
- MSB-CAL-004: the committed calibrated.yaml matches the dataclass defaults
  — no silent drift between the config and the code floor.
- MSB-CAL-005: sweeping every numeric constant never changes the gate
  verdict vector. If a future change makes a constant verdict-affecting,
  this test forces it to be a deliberate, documented decision.
- MSB-CAL-006: the detection keywords (focus/danger/concern) live in
  config/risk_templates.json, not in code — every built-in expert's keyword
  lists must equal the JSON entries exactly.
- MSB-CAL-007: the policy file is load-bearing. A missing, corrupt,
  malformed, or incomplete policy raises RuntimeError BEFORE any expert is
  mutated — an expert with no keywords would silently stop detecting.
'''

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gate_corpus import CORPUS  # noqa: E402

from msb_v3.core.calibration import (  # noqa: E402
    MoIECalibration,
    calibration_path,
    load_moie_calibration,
    moie_calibration,
)
from msb_v3.core.config import settings  # noqa: E402
from msb_v3.moie import MoIEController  # noqa: E402
from msb_v3.moie.experts import BUILTIN_EXPERTS, apply_policy_overrides  # noqa: E402

TEMPLATES_JSON = ROOT / "config" / "risk_templates.json"
CALIBRATED_YAML = ROOT / "config" / "calibrated.yaml"

# The numeric axes of the sweep (field -> representative values). These are
# the constants MSB-CAL-001 proves are verdict-inert.
_SWEEP: Dict[str, List[float]] = {
    "contradiction_penalty": [0.0, 0.15, 0.3],
    "confidence_min": [0.05, 0.1, 0.25],
    "confidence_max": [0.9, 1.0],
    "concern_material_min_confidence": [0.4, 0.6, 0.8],
    "expert_confidence_base": [0.4, 0.5, 0.6],
    "expert_confidence_danger_step": [0.1, 0.15, 0.25],
    "expert_confidence_concern_step": [0.05, 0.08, 0.15],
    "expert_confidence_cap": [0.9, 0.95, 1.0],
}


def _verdict_vector() -> Tuple[str, ...]:
    controller = MoIEController(retriever=lambda _c: [])
    return tuple(
        "BLOCK" if controller.analyze(str(e["claim"])).verdict == "BLOCK" else "ok"
        for e in CORPUS
    )


def _gate_metrics() -> Dict[str, Any]:
    controller = MoIEController(retriever=lambda _c: [])
    tp = fp = tn = fn = 0
    for entry in CORPUS:
        blocked = controller.analyze(str(entry["claim"])).verdict == "BLOCK"
        dangerous = bool(entry["dangerous"])
        if dangerous and blocked:
            tp += 1
        elif dangerous and not blocked:
            fn += 1
        elif not dangerous and blocked:
            fp += 1
        else:
            tn += 1
    return {"tp": tp, "fp": fp, "tn": tn, "fn": fn}


# ── MSB-CAL-002: the JSON is the live template policy surface ───────────────

def test_risk_templates_json_exists_and_parses() -> None:
    assert TEMPLATES_JSON.is_file()
    data = json.loads(TEMPLATES_JSON.read_text(encoding="utf-8"))
    assert data["version"]
    assert isinstance(data["experts"], dict)
    assert "security" in data["experts"]


def test_risk_templates_json_overlay_is_live() -> None:
    '''Editing config/risk_templates.json must change what the experts say —
    the JSON is the policy surface, not a dead copy. Spot-check every expert
    in the JSON against the loaded BUILTIN experts.'''
    data = json.loads(TEMPLATES_JSON.read_text(encoding="utf-8"))
    by_id = {e.expert_id: e for e in BUILTIN_EXPERTS}
    for expert_id, overrides in data["experts"].items():
        expert = by_id.get(expert_id)
        assert expert is not None, f"JSON references unknown expert {expert_id}"
        for field_name in ("risk_templates", "mitigation_templates"):
            values = overrides.get(field_name, {})
            for key, texts in values.items():
                assert expert.__dict__[field_name].get(key) == texts, (
                    f"{expert_id}.{field_name}[{key!r}] not live from JSON — overlay broken"
                )


# ── MSB-CAL-003: constants load from config/calibrated.yaml, clamped ────────

def test_calibration_path_resolves_to_repo_config() -> None:
    assert calibration_path() == Path(settings.msb_home) / "config" / "calibrated.yaml"


def test_calibration_loads_yaml_values() -> None:
    assert CALIBRATED_YAML.is_file()
    cal = load_moie_calibration()
    assert cal.source == str(CALIBRATED_YAML)
    assert cal.contradiction_penalty == 0.15
    assert cal.confidence_min == 0.1
    assert cal.confidence_max == 1.0
    assert cal.concern_material_min_confidence == 0.6
    assert cal.expert_confidence_danger_step == 0.15


def test_calibration_clamps_out_of_range_values(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    '''A hostile/corrupt config must fail closed: out-of-range values are
    clamped, non-numeric values are ignored, never silently accepted.'''
    cfg = tmp_path / "bad.yaml"
    cfg.write_text(
        "moie:\n"
        "  contradiction_penalty: 99\n"
        "  confidence_min: 0.9\n"
        "  confidence_max: 0.1\n"
        "  expert_no_signal_confidence: banana\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MSB_CALIBRATION_PATH", str(cfg))
    cal = load_moie_calibration()
    assert cal.contradiction_penalty == 1.0  # clamped to [0,1]
    assert cal.confidence_min == 0.9
    assert cal.confidence_max == 0.9  # clamped >= confidence_min
    assert cal.expert_no_signal_confidence == 0.4  # non-numeric -> default


def test_calibration_missing_file_falls_back_to_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MSB_CALIBRATION_PATH", str(tmp_path / "nope.yaml"))
    cal = load_moie_calibration()
    assert cal.contradiction_penalty == 0.15
    assert cal.source == "defaults"


# ── MSB-CAL-004: committed yaml matches the code floor (no drift) ───────────

def test_calibrated_yaml_matches_dataclass_defaults() -> None:
    '''The committed config/calibrated.yaml must equal the dataclass
    defaults. If someone tunes a constant, this test forces the change to
    land in BOTH places deliberately.'''
    import yaml as pyyaml

    data = pyyaml.safe_load(CALIBRATED_YAML.read_text(encoding="utf-8"))["moie"]
    defaults = MoIECalibration()
    for field_name in _SWEEP:
        assert data[field_name] == getattr(defaults, field_name), (
            f"calibrated.yaml {field_name}={data[field_name]} drifted from code default "
            f"{getattr(defaults, field_name)}"
        )


# ── MSB-CAL-005: numeric constants never move the gate verdict ──────────────

def test_verdict_invariant_to_numeric_constants(monkeypatch: pytest.MonkeyPatch) -> None:
    '''The executable form of MSB-CAL-001: sweeping every numeric constant
    leaves the gate's per-entry verdict vector identical. A regression here
    means a constant became verdict-affecting — a deliberate, documented
    change, not an accident.'''
    baseline = _verdict_vector()
    for field, values in _SWEEP.items():
        for value in values:
            monkeypatch.setattr(moie_calibration, field, value)
            assert _verdict_vector() == baseline, (
                f"verdict changed when {field}={value} — numeric constants must be verdict-inert (MSB-CAL-001)"
            )
    monkeypatch.undo()
    assert _verdict_vector() == baseline


def test_gate_pin_holds_under_calibration_defaults() -> None:
    '''The gate's measured precision/recall (17/8/8/23) must hold with the
    committed calibration — cross-checks test_gate_contract.py.'''
    m = _gate_metrics()
    assert m == {"tp": 17, "fp": 8, "tn": 8, "fn": 23}


# ── MSB-CAL-006: detection keywords are code-free — the JSON is the source ──

def test_keywords_are_live_from_json_for_every_expert() -> None:
    '''Every built-in expert's focus/danger/concern keywords must equal the
    JSON entries exactly. The JSON is the single detection surface; if
    someone hardcodes keywords back into experts.py (or edits the JSON
    without the experts seeing it), this fails.'''
    data = json.loads(TEMPLATES_JSON.read_text(encoding="utf-8"))
    by_id = {e.expert_id: e for e in BUILTIN_EXPERTS}
    for expert_id, overrides in data["experts"].items():
        expert = by_id.get(expert_id)
        assert expert is not None, f"JSON references unknown expert {expert_id}"
        keywords = overrides.get("keywords")
        assert keywords is not None, f"JSON missing keywords for {expert_id}"
        assert list(expert.focus_keywords) == keywords.get("focus", []), f"{expert_id} focus drifted"
        assert list(expert.danger_keywords) == keywords.get("danger", []), f"{expert_id} danger drifted"
        assert list(expert.concern_keywords) == keywords.get("concern", []), f"{expert_id} concern drifted"


def test_keyword_policy_edits_change_detection() -> None:
    '''Editing config/risk_templates.json must change what the experts detect
    — the JSON is the live policy surface, not a dead copy. Build a fresh
    registry from the committed policy, add a danger keyword as a policy
    edit would, and prove the verdict moves from non-BLOCK to BLOCK.'''
    from msb_v3.moie.experts import SECURITY, DomainExpert, ExpertRegistry

    base = SECURITY
    edited = DomainExpert(
        expert_id=base.expert_id,
        name=base.name,
        description=base.description,
        always_on=base.always_on,
        focus_keywords=base.focus_keywords,
        danger_keywords=tuple(list(base.danger_keywords) + ["exfiltrate"]),
        concern_keywords=base.concern_keywords,
        risk_templates=base.risk_templates,
        mitigation_templates=base.mitigation_templates,
    )
    registry = ExpertRegistry(tuple(e for e in BUILTIN_EXPERTS if e.expert_id != "security") + (edited,))
    controller = MoIEController(registry=registry, retriever=lambda _c: [])
    claim = "the plan will exfiltrate the customer database"
    assert controller.analyze(claim).verdict == "BLOCK"
    # and without the keyword (committed policy), it must NOT block
    assert MoIEController(retriever=lambda _c: []).analyze(claim).verdict != "BLOCK"


def test_no_keyword_lists_hardcoded_in_builtin_constructions() -> None:
    '''The built-in expert constructions in experts.py must not carry
    keyword lists — detection policy is code-free. The only keyword
    references left in the source are the DomainExpert API and the loader.'''
    src = Path("src/msb_v3/moie/experts.py").read_text(encoding="utf-8")
    constructions = src.split("# --- the ten experts ---", 1)[1].split("# The canonical set", 1)[0]
    assert "danger_keywords=" not in constructions
    assert "concern_keywords=" not in constructions
    assert "focus_keywords=" not in constructions


# ── MSB-CAL-007: the policy file is load-bearing (fail-closed) ──────────────

def test_policy_missing_file_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="missing"):
        apply_policy_overrides(tmp_path / "nope.json")


def test_policy_corrupt_json_fails_closed(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(RuntimeError, match="corrupt"):
        apply_policy_overrides(bad)


def test_policy_malformed_root_fails_closed(tmp_path: Path) -> None:
    bad = tmp_path / "malformed.json"
    bad.write_text(json.dumps({"version": "x"}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="malformed"):
        apply_policy_overrides(bad)


def test_policy_incomplete_expert_set_fails_closed_atomically(tmp_path: Path) -> None:
    '''A policy missing an entry for a built-in expert must raise BEFORE any
    expert is mutated — a half-applied policy would silently disable one
    expert's detection.'''
    data = json.loads(TEMPLATES_JSON.read_text(encoding="utf-8"))
    del data["experts"]["security"]
    bad = tmp_path / "incomplete.json"
    bad.write_text(json.dumps(data), encoding="utf-8")

    before = {e.expert_id: e.danger_keywords for e in BUILTIN_EXPERTS}
    with pytest.raises(RuntimeError, match="security"):
        apply_policy_overrides(bad)
    after = {e.expert_id: e.danger_keywords for e in BUILTIN_EXPERTS}
    assert before == after, "policy application must be atomic — partial mutation detected"


def test_policy_non_string_keyword_fails_closed(tmp_path: Path) -> None:
    data = json.loads(TEMPLATES_JSON.read_text(encoding="utf-8"))
    data["experts"]["domain"]["keywords"]["danger"] = ["ok", 42]
    bad = tmp_path / "badtype.json"
    bad.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(RuntimeError, match="list of strings"):
        apply_policy_overrides(bad)


def test_policy_missing_keywords_section_fails_closed(tmp_path: Path) -> None:
    data = json.loads(TEMPLATES_JSON.read_text(encoding="utf-8"))
    del data["experts"]["domain"]["keywords"]
    bad = tmp_path / "nokeywords.json"
    bad.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(RuntimeError, match="keywords"):
        apply_policy_overrides(bad)


def test_policy_reapplies_cleanly_to_committed_file() -> None:
    '''Idempotency: applying the committed policy to the already-loaded
    experts is a no-op — proves the loader is stable under reload and the
    committed file always passes its own validation.'''
    apply_policy_overrides(TEMPLATES_JSON)
    data = json.loads(TEMPLATES_JSON.read_text(encoding="utf-8"))
    by_id = {e.expert_id: e for e in BUILTIN_EXPERTS}
    for expert_id, overrides in data["experts"].items():
        expert = by_id[expert_id]
        assert list(expert.danger_keywords) == overrides["keywords"]["danger"]
