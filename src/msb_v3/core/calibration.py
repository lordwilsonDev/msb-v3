"""MoIE calibration — the numeric "magic numbers" made explicit and loadable.

MSB-CAL-001 finding (20260817): the MoIE *verdict* (BLOCK / CONDITIONAL /
APPROVE) is a pure function of expert keyword hits — none of the numeric
constants in this module change it. They shape the *confidence* number and
the contradiction report only. Calibrating them against gate
precision/recall therefore cannot move the gate; the verdict lever is
keyword membership (moie/experts.py), and the confidence constants exist
here so they are explicit, versioned, and tunable without a code edit.

Load order (first match wins):
  1. env MSB_CALIBRATION_PATH (absolute path to a calibrated.yaml)
  2. <msb_home>/config/calibrated.yaml
  3. built-in defaults below

Every value is clamped to sane bounds on load so a corrupt or hostile
config file fails closed (produces in-range confidence), not silently
out-of-range numbers.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Dict

from msb_v3.core.config import settings

logger = logging.getLogger(__name__)

try:
    import yaml  # PyYAML (declared dependency)
except Exception:  # pragma: no cover - only reachable on broken installs
    yaml = None  # type: ignore[assignment]


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


@dataclass
class MoIECalibration:
    """The tunable MoIE numeric constants. Verdict-inert (see module doc):
    they move confidence and contradiction reporting, never the verdict."""

    # meta_critic.synthesize — confidence = mean(expert confidence)
    #   - contradiction_penalty: subtracted per material contradiction.
    #   - confidence_min / confidence_max: the final clamp bounds.
    contradiction_penalty: float = 0.15
    confidence_min: float = 0.1
    confidence_max: float = 1.0

    # meta_critic.detect_contradictions — a CONCERN-vs-SAFE pair is only
    # material when the CONCERN side is at least this confident.
    concern_material_min_confidence: float = 0.6

    # experts.DomainExpert.analyze — confidence from signal counts:
    #   min(cap, base + danger_step*n_danger + concern_step*n_concern)
    expert_confidence_base: float = 0.5
    expert_confidence_danger_step: float = 0.15
    expert_confidence_concern_step: float = 0.08
    expert_confidence_cap: float = 0.95
    # No danger, no concern, no assumptions: say so honestly.
    expert_no_signal_confidence: float = 0.4

    source: str = "defaults"

    def as_dict(self) -> Dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


def _from_dict(data: Dict[str, Any], source: str) -> MoIECalibration:
    """Build a calibration from a config dict, clamping every numeric field
    to sane bounds and ignoring unknown keys (forward-compatible)."""
    known = {f.name for f in fields(MoIECalibration)}
    kwargs: Dict[str, Any] = {}
    for key, raw in data.items():
        if key not in known or key == "source":
            continue
        try:
            kwargs[key] = float(raw)
        except (TypeError, ValueError):
            logger.warning("moie calibration: non-numeric value for %r (%r); ignoring", key, raw)
            continue

    cal = MoIECalibration(**kwargs, source=source)
    cal.contradiction_penalty = _clamp(cal.contradiction_penalty, 0.0, 1.0)
    cal.confidence_min = _clamp(cal.confidence_min, 0.0, 1.0)
    cal.confidence_max = _clamp(cal.confidence_max, cal.confidence_min, 1.0)
    cal.concern_material_min_confidence = _clamp(cal.concern_material_min_confidence, 0.0, 1.0)
    cal.expert_confidence_base = _clamp(cal.expert_confidence_base, 0.0, 1.0)
    cal.expert_confidence_danger_step = _clamp(cal.expert_confidence_danger_step, 0.0, 1.0)
    cal.expert_confidence_concern_step = _clamp(cal.expert_confidence_concern_step, 0.0, 1.0)
    cal.expert_confidence_cap = _clamp(cal.expert_confidence_cap, 0.0, 1.0)
    cal.expert_no_signal_confidence = _clamp(cal.expert_no_signal_confidence, 0.0, 1.0)
    return cal


def calibration_path() -> Path:
    """The calibrated.yaml path: env override, else <msb_home>/config/."""
    override = os.getenv("MSB_CALIBRATION_PATH")
    if override:
        return Path(override)
    return Path(settings.msb_home) / "config" / "calibrated.yaml"


def load_moie_calibration() -> MoIECalibration:
    """Load the MoIE calibration from disk; fall back to defaults on any
    failure (a broken config must not break the safety gate)."""
    path = calibration_path()
    if yaml is None or not path.is_file():
        return MoIECalibration(source=str(path) if path.is_file() else "defaults")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        root = data.get("moie", data) if isinstance(data, dict) else {}
        if not isinstance(root, dict):
            return MoIECalibration(source=str(path))
        return _from_dict(root, str(path))
    except Exception as exc:  # noqa: BLE001 - config must never break the gate
        logger.warning("moie calibration: failed to load %s (%s); using defaults", path, exc)
        return MoIECalibration(source=str(path))


# The process-wide calibration. Consumers read attributes at call time so
# tests can monkeypatch the singleton; a reload is possible by re-assigning.
moie_calibration = load_moie_calibration()
