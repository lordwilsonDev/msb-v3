"""Spend budget for the automation brain.

The cap (``MSB_AUTOMATION_BUDGET_USD`` — Wilson's $10 key) bounds the LLM
brain's token spend: every plan/creation records an estimated USD cost, and
a creation that would exceed the cap is refused (fail-closed — the brain
never spends past the budget because a cheaper path was available).

Platform per-run costs (Zapier/Make/GoHighLevel operations) are the
provider's own billing, not this ledger; the manifest records them when
known. The ledger lives at data/runtime/automation/budget.db (runtime-store
convention, same as cron/wake).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from msb_v3.core.config import settings

logger = logging.getLogger(__name__)

# Conservative per-call estimates (DeepSeek pricing, USD). The brain's plan
# + creation turn is small; flat estimates keep the ledger honest without
# needing token counters everywhere.
PLAN_ESTIMATE_USD = 0.001
CREATE_ESTIMATE_USD = 0.002


def default_budget_path() -> Path:
    if settings.automation_manifest_path:
        base = Path(settings.automation_manifest_path).parent
    else:
        base = Path(settings.db_path).parent / "runtime" / "automation"
    return base / "budget.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS budget_entries (
                id TEXT PRIMARY KEY,
                ts TEXT NOT NULL,
                kind TEXT NOT NULL,
                amount_usd REAL NOT NULL,
                detail_json TEXT NOT NULL
            );
            """
        )


class BudgetLedger:
    """USD spend ledger with a hard cap."""

    def __init__(self, db_path: Optional[str] = None, cap_usd: Optional[float] = None) -> None:
        self.db_path = Path(db_path) if db_path else default_budget_path()
        self.cap_usd = float(cap_usd if cap_usd is not None else settings.automation_budget_usd)
        _init_db(self.db_path)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def spent(self) -> float:
        with self._conn() as conn:
            row = conn.execute("SELECT COALESCE(SUM(amount_usd), 0) AS s FROM budget_entries").fetchone()
        return float(row["s"]) if row else 0.0

    def remaining(self) -> float:
        return max(0.0, self.cap_usd - self.spent())

    def check(self, amount_usd: float) -> bool:
        """True when recording ``amount_usd`` stays within the cap."""
        return (self.spent() + float(amount_usd)) <= self.cap_usd

    def record(self, amount_usd: float, kind: str = "llm_estimate", **detail: Any) -> Dict[str, Any]:
        """Record a spend entry. Raises ValueError when it would exceed the
        cap (the caller refuses the work — fail-closed)."""
        amount = float(amount_usd)
        if amount < 0:
            raise ValueError("negative spend entry")
        if not self.check(amount):
            raise ValueError(
                f"automation budget exceeded: spent ${self.spent():.3f} + ${amount:.3f} > cap ${self.cap_usd:.2f}"
            )
        entry = {
            "id": f"budget-{uuid.uuid4().hex[:12]}",
            "ts": _now(),
            "kind": kind,
            "amount_usd": amount,
            "detail": detail,
        }
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO budget_entries(id, ts, kind, amount_usd, detail_json) VALUES (?,?,?,?,?)",
                (entry["id"], entry["ts"], kind, amount, json.dumps(detail, default=str)),
            )
        return entry

    def status(self) -> Dict[str, Any]:
        spent = self.spent()
        return {
            "cap_usd": self.cap_usd,
            "spent_usd": round(spent, 4),
            "remaining_usd": round(self.remaining(), 4),
        }
