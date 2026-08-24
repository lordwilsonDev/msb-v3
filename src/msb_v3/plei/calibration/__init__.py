"""PLEI calibration package — Phase 7.

Prediction → outcome → error → better predictions.

Five engines:
    store.py       — prediction/outcome records, hash-chained, JSONL + SQLite
    error.py       — MAPE, Brier score, calibration error
    reliability.py — reliability diagram buckets, over/under-confidence
    scheduler.py   — when to auto-calibrate
    feedback.py    — adjust distribution params from calibration data
"""