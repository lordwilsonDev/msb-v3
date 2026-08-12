"""OuroborosGovernor — the deterministic/subtractive throttle.

Yin counterweight to MoIE's (Yang) generative expansion: convergence is
enforced, not requested. Each loop iteration reports its signals — the
novelty of the proposal versus what is already known, and the duplicate
ratio of recent proposals. The governor returns a deterministic verdict:

- CONTINUE — expanding while still converging;
- SLOW — novelty trending down, expansion outpacing convergence;
- HALT — stalled below the novelty floor for too long, or a duplicate
  ratio above the halt threshold (runaway repetition).

It also suggests ``trim_candidates`` — proposal ids in the bounded history
that sit above the duplicate threshold — as subtractive candidates the
caller may park. v1 suggests only; it never deletes anything itself.

Fail-closed: if its state cannot be read, the verdict is HALT.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from msb_v3.core.config import settings
from msb_v3.governance.db import default_db_path


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class GovernorVerdict:
    action: str  # CONTINUE | SLOW | HALT
    reason: str
    metrics: Dict[str, object]
    trim_candidates: List[str] = field(default_factory=list)


class OuroborosGovernor:
    """Bounded signal history + deterministic verdict rules."""

    def __init__(
        self,
        db_path: Optional[str] = None,
        stall_limit: int = 6,
        novelty_min: float = 0.05,
        dup_ratio_halt: float = 0.5,
        history: int = 20,
    ) -> None:
        self.db_path = str(default_db_path() if db_path is None else db_path)
        self._stall_limit = stall_limit
        self._novelty_min = novelty_min
        self._dup_ratio_halt = dup_ratio_halt
        self._history = history
        self._init_db()

    @classmethod
    def from_settings(cls) -> "OuroborosGovernor":
        s = settings
        return cls(
            stall_limit=s.gov_governor_stall_limit,
            novelty_min=s.gov_governor_novelty_min,
            dup_ratio_halt=s.gov_governor_dup_ratio_halt,
            history=s.gov_governor_history,
        )

    def _init_db(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS governor_runs ("
                " seq INTEGER PRIMARY KEY AUTOINCREMENT,"
                " proposal_id TEXT NOT NULL,"
                " novelty REAL NOT NULL,"
                " duplicate_ratio REAL NOT NULL,"
                " created_at TEXT NOT NULL)"
            )

    def advise(self, proposal_id: str, novelty: float, duplicate_ratio: float = 0.0) -> GovernorVerdict:
        """Record one iteration's signals and return the deterministic verdict."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT INTO governor_runs(proposal_id, novelty, duplicate_ratio, created_at)"
                    " VALUES (?,?,?,?)",
                    (proposal_id, novelty, duplicate_ratio, _now_iso()),
                )
                conn.execute(
                    "DELETE FROM governor_runs WHERE seq NOT IN ("
                    " SELECT seq FROM governor_runs ORDER BY seq DESC LIMIT ?)",
                    (self._history,),
                )
                rows = conn.execute(
                    "SELECT proposal_id, novelty, duplicate_ratio FROM governor_runs"
                    " ORDER BY seq DESC LIMIT ?",
                    (self._history,),
                ).fetchall()
        except Exception as exc:  # fail-closed: unreadable state => halt
            return GovernorVerdict(
                "HALT", f"governor state unreadable — fail-closed ({exc})", {"fail_closed": True}
            )

        rows = list(rows)  # newest first
        stall = 0
        for _, novelty_i, _dup in rows:
            if novelty_i < self._novelty_min:
                stall += 1
            else:
                break
        dup_ratio = rows[0][2]
        trim = [
            pid for pid, _n, dup in rows
            if dup >= self._dup_ratio_halt and pid != proposal_id
        ]

        if dup_ratio >= self._dup_ratio_halt:
            return GovernorVerdict(
                "HALT",
                f"duplicate ratio {dup_ratio:.2f} >= halt threshold {self._dup_ratio_halt}",
                {"stall_count": stall, "dup_ratio": dup_ratio, "trend": "n/a"},
                trim,
            )
        if stall >= self._stall_limit:
            return GovernorVerdict(
                "HALT",
                f"{stall} consecutive stalled iterations below novelty floor {self._novelty_min}",
                {"stall_count": stall, "dup_ratio": dup_ratio, "trend": "flat"},
                trim,
            )
        recent3 = sum(n for _, n, _ in rows[:3]) / max(len(rows[:3]), 1)
        if len(rows) >= 6:
            prior3 = sum(n for _, n, _ in rows[3:6]) / 3
            if recent3 < prior3:
                return GovernorVerdict(
                    "SLOW",
                    "novelty trending down — expansion outpacing convergence",
                    {"stall_count": stall, "dup_ratio": dup_ratio, "trend": "declining"},
                    trim,
                )
        return GovernorVerdict(
            "CONTINUE",
            "converging within bounds",
            {"stall_count": stall, "dup_ratio": dup_ratio, "trend": "stable"},
            trim,
        )

    def history(self) -> List[Dict[str, object]]:
        """Bounded recent signal history (newest first) for status surfaces."""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT proposal_id, novelty, duplicate_ratio, created_at FROM governor_runs"
                " ORDER BY seq DESC LIMIT ?",
                (self._history,),
            ).fetchall()
        return [
            {"proposal_id": r[0], "novelty": r[1], "duplicate_ratio": r[2], "created_at": r[3]}
            for r in rows
        ]
