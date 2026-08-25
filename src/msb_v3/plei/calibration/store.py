"""Calibration Store — prediction/outcome records with hash-chain integrity.

Every Monte Carlo forecast produces a Prediction record; when the
predicted event's actual outcome is observed (execution completes,
milestone hits, risk materializes), an Outcome record is paired to it.
Together they form the calibration dataset that powers Phase 7.

Storage: JSONL file (``.plei/calibration.jsonl``) — append-only,
deterministic, no database dependency. Each record carries a SHA-256
hash of the previous record for integrity.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# ── Data types ─────────────────────────────────────────────────────────────


@dataclass(slots=True)
class Prediction:
    """A forecast prediction — what PLEI's Monte Carlo engine predicted."""

    prediction_id: str
    project: str
    forecast_at: str  # ISO-8601

    # Duration prediction
    predicted_p50_days: float
    predicted_p80_days: float
    predicted_p95_days: float
    predicted_mean_days: float
    predicted_stdev_days: float

    # Failure prediction
    predicted_failure_probability: float  # 0–1

    # Milestone predictions
    milestone_predictions: dict[str, float] = field(default_factory=dict)
    # e.g. {"target_completion": 0.87, "integration_done": 0.95}

    # Confidence
    confidence_level: str = ""  # low / moderate / high / extreme
    coefficient_of_variation: float = 0.0

    # Context
    trial_count: int = 0
    seed: int = 0
    variables_used: int = 0  # how many risk variables fed the sim

    # Calibration metadata
    calibration_status: str = "predicted"  # predicted / matched / stale
    matched_outcome_id: str = ""


@dataclass(slots=True)
class Outcome:
    """An observed outcome — what actually happened."""

    outcome_id: str
    prediction_id: str  # links to Prediction.prediction_id
    project: str
    observed_at: str  # ISO-8601

    # Duration outcome
    actual_duration_days: float  # -1 if not yet known
    actual_completion: bool = False  # did the project complete?

    # Failure outcome
    failures_encountered: int = 0  # how many failure events actually fired
    severity: str = ""  # none / minor / major / critical

    # Milestone outcomes
    milestone_outcomes: dict[str, bool] = field(default_factory=dict)
    # e.g. {"target_completion": True, "integration_done": False}

    # Context
    actual_stage: str = ""  # lifecycle stage at observation time
    step_count: int = 0  # how many governed steps executed
    error_note: str = ""


@dataclass(slots=True)
class CalibrationPair:
    """A paired prediction + outcome — the atomic calibration unit."""

    prediction: Prediction
    outcome: Outcome

    # Computed
    duration_error_days: float = 0.0  # actual - predicted_p50
    duration_mape: float = 0.0  # |actual - predicted_p50| / actual
    failure_brier: float = 0.0  # (predicted_failure_prob - 1_if_failed)^2
    milestone_brier: float = 0.0  # avg Brier across milestones

    @property
    def is_calibrated(self) -> bool:
        return self.duration_mape >= 0 and self.failure_brier >= 0


# ── Store ──────────────────────────────────────────────────────────────────


class CalibrationStore:
    """Append-only, hash-chained store for calibration records.

    Stores predictions and outcomes as JSONL on disk, plus pairs them
    into CalibrationPairs for the error/reliability/feedback engines.
    """

    def __init__(self, path: str | Path = ".plei/calibration.jsonl") -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._prev_hash: str = ""
        self._read_chain()

    # ── Write ──────────────────────────────────────────────────────────

    def record_prediction(self, prediction: Prediction) -> None:
        """Append a prediction to the chain."""
        record = self._serialize("PREDICTION", asdict(prediction))
        self._append_and_hash(record)

    def record_outcome(self, outcome: Outcome) -> None:
        """Append an outcome to the chain."""
        record = self._serialize("OUTCOME", asdict(outcome))
        self._append_and_hash(record)
        # No in-place mutation — matching is derived from outcome→prediction_id linkage

    # ── Read ───────────────────────────────────────────────────────────

    def predictions(self) -> list[Prediction]:
        """All predictions in the store, chronologically.

        calibration_status is derived from whether a matching outcome exists.
        """
        matched_ids = {o.prediction_id for o in self.outcomes()}
        results: list[Prediction] = []
        for r in self._read_records():
            if r.get("type") != "PREDICTION":
                continue
            try:
                fields = {k: v for k, v in r.items()
                         if k in Prediction.__dataclass_fields__}
                pred = Prediction(**fields)  # type: ignore[arg-type]
                if pred.prediction_id in matched_ids:
                    # Derive status from outcomes rather than stored field
                    object.__setattr__(pred, "calibration_status", "matched")
                results.append(pred)
            except (TypeError, ValueError):
                continue
        return results

    def outcomes(self) -> list[Outcome]:
        """All outcomes in the store, chronologically."""
        results: list[Outcome] = []
        for r in self._read_records():
            if r.get("type") == "OUTCOME":
                try:
                    fields = {k: v for k, v in r.items()
                             if k in Outcome.__dataclass_fields__}
                    results.append(Outcome(**fields))  # type: ignore[arg-type]
                except (TypeError, ValueError):
                    continue
        return results

    def pairs(self) -> list[CalibrationPair]:
        """All matched prediction/outcome pairs."""
        preds = {p.prediction_id: p for p in self.predictions()}
        outs = [o for o in self.outcomes() if o.prediction_id in preds]

        pairs: list[CalibrationPair] = []
        for o in outs:
            p = preds[o.prediction_id]
            pairs.append(_compute_pair(p, o))
        return pairs

    def prediction_count(self) -> int:
        return sum(1 for r in self._read_records() if r.get("type") == "PREDICTION")

    def outcome_count(self) -> int:
        return sum(1 for r in self._read_records() if r.get("type") == "OUTCOME")

    def pair_count(self) -> int:
        return len(self.pairs())

    # ── Integrity ──────────────────────────────────────────────────────

    def verify_chain(self) -> tuple[bool, str]:
        """Verify SHA-256 chain integrity. Returns (ok, message)."""
        records = self._raw_records()
        if not records:
            return True, "empty chain"
        for i, record in enumerate(records):
            h = record.get("_hash", "")
            prev = records[i - 1].get("_hash", "") if i > 0 else ""
            fields_json = json.dumps(record["_fields"], sort_keys=True, default=str)
            expected = self._compute_hash(fields_json, prev)
            if h and h != expected:
                return False, f"hash mismatch at record {i}: expected {expected[:12]}, got {h[:12]}"
        return True, "chain intact"

    # ── Internals ──────────────────────────────────────────────────────

    def _serialize(self, record_type: str, fields: dict[str, Any]) -> dict[str, Any]:
        return {"type": record_type, "_fields": fields, "_hash": ""}

    def _append_and_hash(self, record: dict[str, Any]) -> None:
        fields_json = json.dumps(record["_fields"], sort_keys=True, default=str)
        record["_hash"] = self._compute_hash(fields_json, self._prev_hash)
        with open(self.path, "a") as f:
            f.write(json.dumps(record) + "\n")
        self._prev_hash = record["_hash"]

    def _compute_hash(self, fields_json: str, prev_hash: str) -> str:
        data = (prev_hash + fields_json).encode("utf-8")
        return hashlib.sha256(data).hexdigest()[:40]

    def _read_records(self) -> list[dict[str, Any]]:
        """Parse records, returning dicts with dataclass fields + type/hash."""
        results: list[dict[str, Any]] = []
        for raw in self._raw_records():
            fields = raw.get("_fields", raw)
            record_type = raw.get("type", fields.get("type", ""))
            try:
                result: dict[str, Any] = dict(fields)
                result["type"] = record_type
                result["_hash"] = raw.get("_hash", "")
                results.append(result)
            except Exception:
                continue
        return results

    def _raw_records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        records: list[dict[str, Any]] = []
        with open(self.path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return records

    def _read_chain(self) -> None:
        records = self._raw_records()
        self._prev_hash = records[-1].get("_hash", "") if records else ""

    def _match_outcome(self, outcome: Outcome) -> None:
        """No-op: matching is derived from outcome→prediction_id linkage at read time.

        Previously this method mutated prediction records in-place, which broke
        the hash chain (fields changed but hash was preserved). Now the match
        is purely derived — no file mutation.
        """


# ── Helpers ────────────────────────────────────────────────────────────────


def _compute_pair(pred: Prediction, out: Outcome) -> CalibrationPair:
    """Compute error metrics for a matched prediction/outcome pair."""
    # Duration error — MAPE on P50
    dur_error = out.actual_duration_days - pred.predicted_p50_days
    dur_mape = 0.0
    if out.actual_duration_days > 0:
        dur_mape = abs(dur_error) / out.actual_duration_days

    # Failure Brier score: (predicted_prob - 1_if_failed)^2
    failed = 1.0 if out.failures_encountered > 0 else 0.0
    failure_brier = (pred.predicted_failure_probability - failed) ** 2

    # Milestone Brier: avg across milestones
    milestone_brier = 0.0
    m_count = 0
    for m_name, m_prob in pred.milestone_predictions.items():
        actual = 1.0 if out.milestone_outcomes.get(m_name, False) else 0.0
        milestone_brier += (m_prob - actual) ** 2
        m_count += 1
    if m_count > 0:
        milestone_brier /= m_count

    return CalibrationPair(
        prediction=pred,
        outcome=out,
        duration_error_days=dur_error,
        duration_mape=dur_mape,
        failure_brier=failure_brier,
        milestone_brier=milestone_brier,
    )