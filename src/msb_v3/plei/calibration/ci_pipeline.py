"""CI prediction pipeline — record predictions before CI, outcomes after.

This is a separate pipeline from the project lifecycle predictions in
``store.py``.  CI predictions track test count, pass rate, and duration
per run, then compare against actual outcomes to build a calibration
report.

Usage::

    from msb_v3.plei.calibration.ci_pipeline import CIPipeline

    pipe = CIPipeline()

    # Before CI run — record prediction
    pred = pipe.record_prediction(
        expected_test_count=2291,
        expected_pass_rate=1.0,
        expected_duration_seconds=240,
    )

    # After CI run — record outcome
    pipe.record_outcome(
        prediction_id=pred.prediction_id,
        actual_test_count=2252,
        actual_pass_rate=0.999,
        actual_duration_seconds=227,
    )

    # Generate report
    report = pipe.calibration_report()
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class CIPrediction:
    """A prediction made before a CI run."""

    prediction_id: str = ""
    project: str = "msb-v3"
    forecast_at: str = ""
    expected_test_count: int = 0
    expected_pass_rate: float = 1.0
    expected_duration_seconds: float = 0.0
    expected_failures: int = 0
    confidence: str = "medium"
    calibration_status: str = "pending"


@dataclass
class CIOutcome:
    """The actual outcome of a CI run."""

    outcome_id: str = ""
    prediction_id: str = ""
    project: str = "msb-v3"
    observed_at: str = ""
    actual_test_count: int = 0
    actual_pass_rate: float = 1.0
    actual_duration_seconds: float = 0.0
    actual_failures: int = 0
    error_note: str = ""


@dataclass
class CICalibrationPair:
    """A matched prediction/outcome pair with error metrics."""

    prediction_id: str = ""
    test_count_error: float = 0.0
    pass_rate_error: float = 0.0
    duration_error_pct: float = 0.0
    failure_predicted: int = 0
    failure_actual: int = 0
    failure_correct: bool = False


@dataclass
class CICalibrationReport:
    """Aggregated calibration report for CI predictions."""

    pair_count: int = 0
    mean_test_count_error: float = 0.0
    mean_pass_rate_error: float = 0.0
    mean_duration_error_pct: float = 0.0
    failure_accuracy: float = 0.0
    is_calibrated: bool = False
    verdict: str = "insufficient_data"


class CIPipeline:
    """Append-only, hash-chained store for CI calibration records."""

    def __init__(self, path: str | Path = ".plei/ci-calibration.jsonl") -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._prev_hash: str = ""
        self._read_chain()

    # ── Write ──────────────────────────────────────────────────────────

    def record_prediction(
        self,
        expected_test_count: int,
        expected_pass_rate: float,
        expected_duration_seconds: float,
        expected_failures: int = 0,
        project: str = "msb-v3",
        confidence: str = "medium",
    ) -> CIPrediction:
        """Record a CI prediction before the run starts."""
        pred = CIPrediction(
            prediction_id=f"ci:{project}:{int(time.time())}:{_short_hash()}",
            project=project,
            forecast_at=_utcnow(),
            expected_test_count=expected_test_count,
            expected_pass_rate=expected_pass_rate,
            expected_duration_seconds=expected_duration_seconds,
            expected_failures=expected_failures,
            confidence=confidence,
            calibration_status="pending",
        )
        record = self._serialize("CI_PREDICTION", asdict(pred))
        self._append_and_hash(record)
        return pred

    def record_outcome(
        self,
        prediction_id: str,
        actual_test_count: int,
        actual_pass_rate: float,
        actual_duration_seconds: float,
        actual_failures: int = 0,
        error_note: str = "",
        project: str = "msb-v3",
    ) -> CIOutcome:
        """Record the actual CI outcome after the run completes."""
        out = CIOutcome(
            outcome_id=f"ci-out:{int(time.time())}:{_short_hash()}",
            prediction_id=prediction_id,
            project=project,
            observed_at=_utcnow(),
            actual_test_count=actual_test_count,
            actual_pass_rate=actual_pass_rate,
            actual_duration_seconds=actual_duration_seconds,
            actual_failures=actual_failures,
            error_note=error_note,
        )
        record = self._serialize("CI_OUTCOME", asdict(out))
        self._append_and_hash(record)
        return out

    # ── Read ───────────────────────────────────────────────────────────

    def predictions(self) -> list[CIPrediction]:
        """All CI predictions, chronologically."""
        matched_ids = {o.prediction_id for o in self.outcomes()}
        results: list[CIPrediction] = []
        for r in self._read_records():
            if r.get("type") != "CI_PREDICTION":
                continue
            try:
                fields = {k: v for k, v in r.items() if k in CIPrediction.__dataclass_fields__}
                pred = CIPrediction(**fields)
                if pred.prediction_id in matched_ids:
                    pred.calibration_status = "matched"
                results.append(pred)
            except (TypeError, ValueError):
                continue
        return results

    def outcomes(self) -> list[CIOutcome]:
        """All CI outcomes, chronologically."""
        results: list[CIOutcome] = []
        for r in self._read_records():
            if r.get("type") == "CI_OUTCOME":
                try:
                    fields = {k: v for k, v in r.items() if k in CIOutcome.__dataclass_fields__}
                    results.append(CIOutcome(**fields))
                except (TypeError, ValueError):
                    continue
        return results

    def pairs(self) -> list[CICalibrationPair]:
        """All matched prediction/outcome pairs with error metrics."""
        preds = {p.prediction_id: p for p in self.predictions()}
        outs = [o for o in self.outcomes() if o.prediction_id in preds]
        pairs: list[CICalibrationPair] = []
        for o in outs:
            p = preds[o.prediction_id]
            pairs.append(_compute_pair(p, o))
        return pairs

    def calibration_report(self) -> CICalibrationReport:
        """Generate aggregated calibration report."""
        pairs = self.pairs()
        if not pairs:
            return CICalibrationReport(verdict="insufficient_data")

        tc_errors = [abs(p.test_count_error) for p in pairs]
        pr_errors = [abs(p.pass_rate_error) for p in pairs]
        dur_errors = [abs(p.duration_error_pct) for p in pairs]
        fail_correct = sum(1 for p in pairs if p.failure_correct)

        report = CICalibrationReport(
            pair_count=len(pairs),
            mean_test_count_error=sum(tc_errors) / len(tc_errors),
            mean_pass_rate_error=sum(pr_errors) / len(pr_errors),
            mean_duration_error_pct=sum(dur_errors) / len(dur_errors),
            failure_accuracy=fail_correct / len(pairs) if pairs else 0.0,
        )

        # Calibration verdict
        if report.pair_count < 5:
            report.verdict = "insufficient_data"
            report.is_calibrated = False
        elif report.mean_pass_rate_error < 0.02 and report.mean_duration_error_pct < 0.15:
            report.verdict = "well_calibrated"
            report.is_calibrated = True
        elif report.mean_pass_rate_error < 0.05 and report.mean_duration_error_pct < 0.30:
            report.verdict = "approximately_calibrated"
            report.is_calibrated = True
        else:
            report.verdict = "miscalibrated"
            report.is_calibrated = False

        return report

    # ── Chain integrity ────────────────────────────────────────────────

    def verify_chain(self) -> tuple[bool, str]:
        """Verify the hash chain is intact."""
        # Must use raw records (with nested _fields) to recompute hashes
        records = self._raw_records()
        prev = ""
        for i, rec in enumerate(records):
            stored = rec.get("_hash", "")
            expected = self._compute_hash(
                json.dumps(rec.get("_fields", {}), sort_keys=True, separators=(",", ":")),
                prev,
            )
            if stored != expected:
                return False, f"chain break at record {i}: expected {expected[:12]}, got {stored[:12]}"
            prev = stored
        return True, f"chain OK ({len(records)} records)"

    # ── Internal ───────────────────────────────────────────────────────

    def _serialize(self, record_type: str, fields: dict[str, Any]) -> dict[str, Any]:
        fields_json = json.dumps(fields, sort_keys=True, separators=(",", ":"))
        record_hash = self._compute_hash(fields_json, self._prev_hash)
        self._prev_hash = record_hash
        return {"type": record_type, "_fields": fields, "_hash": record_hash}

    def _append_and_hash(self, record: dict[str, Any]) -> None:
        with open(self.path, "a") as f:
            f.write(json.dumps(record, separators=(",", ":")) + "\n")

    def _compute_hash(self, fields_json: str, prev_hash: str) -> str:
        raw = f"{prev_hash}:{fields_json}"
        return hashlib.sha1(raw.encode()).hexdigest()[:20]

    def _read_chain(self) -> None:
        records = self._raw_records()
        if records:
            self._prev_hash = records[-1].get("_hash", "")

    def _read_records(self) -> list[dict[str, Any]]:
        """Parse records, returning dicts with dataclass fields + type/hash."""
        results: list[dict[str, Any]] = []
        for raw in self._raw_records():
            fields = raw.get("_fields", raw)
            record_type = raw.get("type", fields.get("type", ""))
            try:
                result = dict(fields)
                result["type"] = record_type
                result["_hash"] = raw.get("_hash", "")
                results.append(result)
            except Exception:  # noqa: BLE001
                continue
        return results

    def _raw_records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        results: list[dict[str, Any]] = []
        for line in self.path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                results.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return results


# ── Helpers ──────────────────────────────────────────────────────────────


def _compute_pair(pred: CIPrediction, out: CIOutcome) -> CICalibrationPair:
    tc_err = (out.actual_test_count - pred.expected_test_count) / max(pred.expected_test_count, 1)
    pr_err = out.actual_pass_rate - pred.expected_pass_rate
    dur_err = (
        (out.actual_duration_seconds - pred.expected_duration_seconds)
        / max(pred.expected_duration_seconds, 1)
    )
    fail_correct = (pred.expected_failures > 0) == (out.actual_failures > 0)
    return CICalibrationPair(
        prediction_id=pred.prediction_id,
        test_count_error=tc_err,
        pass_rate_error=pr_err,
        duration_error_pct=dur_err,
        failure_predicted=pred.expected_failures,
        failure_actual=out.actual_failures,
        failure_correct=fail_correct,
    )


def _short_hash() -> str:
    return hashlib.sha1(str(time.time_ns()).encode()).hexdigest()[:8]


def _utcnow() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
