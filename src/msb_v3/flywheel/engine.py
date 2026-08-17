"""FlywheelEngine — the 9-stage turn state machine behind the brakes.

Blueprint §0.5's Research->Build loop, with the Phase 0B brakes load-bearing
at every step: each stage transition goes through Guard.check_run (kill
switch, budget, approvals, governor). A refusal never continues the turn —
it parks (WAITING_APPROVAL), halts (HALTED), or blocks (BLOCKED), with a
reason recorded. Turn state lives in SQLite, so a parked turn survives a
restart and any later engine instance can resume it. Every transition is
audited to the UAC audit chain — the loop is never a black box.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import httpx

from msb_ledger.audit_chain import AuditChainLike
from msb_ledger.axiom_library import ArtifactRecord, AxiomLibrary
from msb_ledger.chain_anchor import anchored_chain_from_env
from msb_v3.core.config import settings
from msb_v3.flywheel.chargers import (
    PaperScanner,
    SovereignCharger,
    StubCharger,
    TavilyScanner,
)
from msb_v3.flywheel.models import (
    APPROVAL_STAGES,
    ITERATIONS_PER_STAGE,
    RESEARCH_STAGES,
    STAGES,
    Turn,
)
from msb_v3.governance.approval import ApprovalError, ApprovalQueue, IdempotencyError
from msb_v3.governance.budget import BudgetLedger
from msb_v3.governance.governor import OuroborosGovernor
from msb_v3.governance.guard import Guard
from msb_v3.governance.killswitch import KillSwitch

logger = logging.getLogger(__name__)

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_db_path() -> Path:
    return Path(settings.db_path).parent / "flywheel" / "turns.db"


def _slugify(problem: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", problem.lower()).strip("-")
    return slug[:48] or "turn"


class FlywheelEngine:
    def __init__(
        self,
        db_path: Optional[str] = None,
        queue: Optional[ApprovalQueue] = None,
        ledger: Optional[BudgetLedger] = None,
        switch: Optional[KillSwitch] = None,
        governor: Optional[OuroborosGovernor] = None,
        audit_chain: Optional[AuditChainLike] = None,
        axiom_library: Optional[AxiomLibrary] = None,
        scanner: Optional[PaperScanner] = None,
        vault_root: Optional[Path] = None,
        runtime_root: Optional[Path] = None,
        novelty_threshold: float = 0.85,
        novelty_fn: Optional[Callable[[str], float]] = None,
    ) -> None:
        self.db_path = str(default_db_path() if db_path is None else db_path)
        self._queue = queue or ApprovalQueue()
        self._ledger = ledger or BudgetLedger.from_settings()
        self._switch = switch or KillSwitch()
        self._governor = governor or OuroborosGovernor.from_settings()
        self._chain = audit_chain or anchored_chain_from_env()
        # The guard audits governance decisions to the SAME chain as the
        # engine — a defaulted guard would silently write to the default
        # (production) chain even when an isolated chain was injected.
        self._guard = Guard(self._switch, self._ledger, self._queue, self._governor, audit_chain=self._chain)
        self._axiom = axiom_library or AxiomLibrary()
        # Default is the real feed (Tavily, arxiv-restricted); it degrades
        # to an honest 0-papers note when the key is absent or the feed is
        # down, so offline turns still run — they just scan nothing.
        self._scanner = scanner if scanner is not None else TavilyScanner()
        self._novelty_fn = novelty_fn or self._vault_novelty
        self._vault_root = Path(vault_root or settings.vault_path)
        self._runtime_root = Path(runtime_root or (Path(settings.msb_home) / "runtime" / "flywheel"))
        self._novelty_threshold = novelty_threshold
        self._init_db()

    # --- persistence -------------------------------------------------------

    def _init_db(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS turns ("
                " turn_id TEXT PRIMARY KEY,"
                " problem TEXT NOT NULL,"
                " status TEXT NOT NULL,"
                " stage TEXT NOT NULL,"
                " charger TEXT NOT NULL,"
                " skill TEXT NOT NULL,"
                " novelty REAL NOT NULL,"
                " approval_ids TEXT NOT NULL,"
                " blueprint_path TEXT, uim_path TEXT, build_path TEXT, combine_path TEXT, record_path TEXT,"
                " notes TEXT NOT NULL,"
                " created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )

    def _row_to_turn(self, row: sqlite3.Row) -> Turn:
        return Turn(
            turn_id=row["turn_id"],
            problem=row["problem"],
            status=row["status"],
            stage=row["stage"],
            charger=row["charger"],
            skill=row["skill"],
            novelty=row["novelty"],
            approval_ids=json.loads(row["approval_ids"]),
            blueprint_path=row["blueprint_path"],
            uim_path=row["uim_path"],
            build_path=row["build_path"],
            combine_path=row["combine_path"],
            record_path=row["record_path"],
            notes=json.loads(row["notes"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _save(self, turn: Turn) -> None:
        turn.updated_at = _now_iso()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO turns(turn_id, problem, status, stage, charger, skill, novelty,"
                " approval_ids, blueprint_path, uim_path, build_path, combine_path, record_path, notes,"
                " created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    turn.turn_id, turn.problem, turn.status, turn.stage, turn.charger, turn.skill,
                    turn.novelty, json.dumps(turn.approval_ids), turn.blueprint_path, turn.uim_path,
                    turn.build_path, turn.combine_path, turn.record_path,
                    json.dumps(turn.notes), turn.created_at, turn.updated_at,
                ),
            )

    def get(self, turn_id: str) -> Optional[Turn]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM turns WHERE turn_id=?", (turn_id,)).fetchone()
        return self._row_to_turn(row) if row else None

    def list(self) -> List[Turn]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM turns ORDER BY created_at DESC").fetchall()
        return [self._row_to_turn(r) for r in rows]

    def _audit(self, event_type: str, payload: Dict[str, Any]) -> None:
        """Audit one flywheel event. Contained: a failing audit chain must
        never kill the loop it is watching — but the failure is surfaced on
        the turn (repo lesson: decisions never vanish without a trace)."""
        try:
            self._chain.append("flywheel", event_type, payload)
        except Exception as exc:  # noqa: BLE001 — audit failure must not kill the loop
            tid = payload.get("turn_id") if isinstance(payload, dict) else None
            if tid:
                try:
                    turn = self.get(tid)
                    if turn is not None:
                        turn.notes.append(f"audit failed: {type(exc).__name__}: {exc}")
                        self._save(turn)
                except Exception as exc:
                    logger.debug("failed to append audit-failed note: %s", exc)

    # --- guards ------------------------------------------------------------

    def _charger_for(self, name: str):
        return SovereignCharger() if name == "sovereign" else StubCharger()

    # --- lifecycle ---------------------------------------------------------

    def start(
        self,
        problem: str,
        charger: str = "stub",
        skill: str = "",
        turn_id: Optional[str] = None,
    ) -> Turn:
        """Create a turn. Gated at entry by the brakes (kill switch +
        iterations budget); a refused start records a BLOCKED turn with the
        reason so the owner sees *why*, never silently."""
        now = _now_iso()
        tid = turn_id or uuid.uuid4().hex[:12]
        turn = Turn(
            turn_id=tid, problem=problem, status="PENDING", stage=STAGES[0],
            charger=charger, skill=skill, created_at=now, updated_at=now,
        )
        verdict = self._guard.check_run("flywheel.start", budget_units={"iterations": ITERATIONS_PER_STAGE})
        if not verdict.allowed:
            turn.status = "BLOCKED"
            turn.notes.append(f"start refused: {verdict.reason}")
            self._save(turn)
            self._audit("blocked", {"turn_id": tid, "reason": verdict.reason})
            return turn
        self._save(turn)
        self._audit("started", {"turn_id": tid, "problem": problem, "charger": charger})
        return turn

    def run(self, turn_id: str) -> Turn:
        """Advance the turn through remaining stages until it parks, halts,
        completes, or errors. Idempotent per stage — a re-run re-checks the
        guards, so a parked turn resumes only when the brakes actually allow
        it."""
        turn = self.get(turn_id)
        if turn is None:
            raise ValueError(f"unknown turn {turn_id}")
        if turn.status in ("DONE", "ALREADY_EXISTS", "RUNNING"):
            return turn
        # Claim the turn with a status CAS: two concurrent drivers (API
        # background task + CLI resume) must not both execute stages. The
        # repo already learned this lesson on the audit chain and budget
        # ledger — the turns table gets the same treatment.
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "UPDATE turns SET status='RUNNING', updated_at=? WHERE turn_id=?"
                " AND status NOT IN ('RUNNING','DONE','ALREADY_EXISTS')",
                (_now_iso(), turn_id),
            )
            if cur.rowcount == 0:
                return self.get(turn_id) or turn
        turn.status = "RUNNING"

        while True:
            if turn.status != "RUNNING":
                break
            stage = turn.stage
            if stage not in STAGES:
                turn.status = "DONE"
                self._save(turn)
                self._audit("done", {"turn_id": turn_id})
                break

            approval_kind = APPROVAL_STAGES.get(stage)
            budget_units: Dict[str, int] = {"iterations": ITERATIONS_PER_STAGE}
            if stage in RESEARCH_STAGES:
                budget_units["research_calls"] = 1
            approval_id = turn.approval_ids.get(stage)
            signal = (
                {"proposal_id": turn_id, "novelty": turn.novelty, "duplicate_ratio": 0.0}
                if stage == "charge"
                else None
            )
            verdict = self._guard.check_run(
                action=f"flywheel.{stage}",
                kind=approval_kind,
                budget_units=budget_units,
                approval_id=approval_id,
                signal=signal,
            )

            if verdict.allowed:
                try:
                    self._exec_stage(turn, stage)
                except Exception as exc:  # noqa: BLE001 — containment boundary
                    turn.status = "ERROR"
                    turn.notes.append(f"{stage} failed: {type(exc).__name__}: {exc}")
                    self._save(turn)
                    self._audit("error", {"turn_id": turn_id, "stage": stage, "error": str(exc)})
                    break
                self._audit("stage." + stage, {"turn_id": turn_id, "verdict": verdict.action})
                self._save(turn)
                if turn.status != "RUNNING":  # a stage (e.g. novelty gate) stopped the turn
                    break
                idx = STAGES.index(stage)
                turn.stage = STAGES[idx + 1] if idx + 1 < len(STAGES) else "_done"
                self._save(turn)
                continue

            # Refused — park or halt with a recorded reason.
            if verdict.action == "APPROVAL_REQUIRED":
                item = self._queue.submit(
                    approval_kind or "build",
                    f"{stage} for flywheel turn {turn_id}",
                    payload={"turn_id": turn_id, "stage": stage},
                    evidence_refs=[p for p in (turn.uim_path, turn.blueprint_path) if p],
                )
                turn.approval_ids[stage] = item.item_id
                turn.status = "WAITING_APPROVAL"
                turn.notes.append(f"{stage} awaiting owner approval ({item.item_id})")
                self._audit("approval_required", {"turn_id": turn_id, "stage": stage, "approval_id": item.item_id})
            elif verdict.action == "APPROVAL_PENDING":
                turn.status = "WAITING_APPROVAL"
                turn.notes.append(f"{stage} awaiting owner approval ({approval_id})")
            elif verdict.action == "HALT":
                turn.status = "HALTED"
                turn.notes.append(f"{stage} halted: {verdict.reason}")
                self._audit("halted", {"turn_id": turn_id, "stage": stage, "reason": verdict.reason})
            else:
                turn.status = "BLOCKED"
                turn.notes.append(f"{stage} blocked: {verdict.reason}")
            self._save(turn)
            break
        return turn

    def approve(self, turn_id: str, operator: str = "operator") -> Turn:
        """Approve this turn's pending approval items, then resume it."""
        turn = self.get(turn_id)
        if turn is None:
            raise ValueError(f"unknown turn {turn_id}")
        for stage, item_id in turn.approval_ids.items():
            try:
                self._queue.approve(item_id, operator)
                turn.notes.append(f"{stage} approved by {operator}")
            except (IdempotencyError, ApprovalError) as exc:
                logger.debug("approval %s already decided for stage %s: %s", item_id, stage, exc)
        self._save(turn)
        return self.resume(turn_id)

    def resume(self, turn_id: str) -> Turn:
        """Resume a parked/halted turn. The status CAS in run() is the
        concurrency guard — resume itself just re-enters the loop."""
        if self.get(turn_id) is None:
            raise ValueError(f"unknown turn {turn_id}")
        return self.run(turn_id)

    # --- stages ------------------------------------------------------------

    def _exec_stage(self, turn: Turn, stage: str) -> None:
        fn = getattr(self, "_stage_" + stage)
        fn(turn)

    def _stage_verify_novelty(self, turn: Turn) -> None:
        novelty = self._novelty_fn(turn.problem)
        turn.novelty = novelty
        if novelty >= self._novelty_threshold:
            turn.status = "ALREADY_EXISTS"
            turn.notes.append(
                f"already covered in vault knowledge (novelty {novelty:.2f} >= {self._novelty_threshold})"
            )
            self._audit("already_exists", {"turn_id": turn.turn_id, "novelty": novelty})
        else:
            turn.notes.append(f"novelty {novelty:.2f} — clear to build")

    def _stage_draft_blueprint(self, turn: Turn) -> None:
        path = self._runtime_root / "blueprints" / f"{turn.turn_id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"# Flywheel Blueprint — {turn.turn_id}\n\n"
            f"**Problem:** {turn.problem}\n"
            f"**Charger:** {turn.charger}\n"
            f"**Skill:** {turn.skill or '—'}\n"
            f"**Novelty:** {turn.novelty:.2f}\n\n"
            f"*Drafted by the flywheel engine; updated from the UIM at the charge stage.*\n"
        )
        turn.blueprint_path = str(path)

    def _stage_charge(self, turn: Turn) -> None:
        charger = self._charger_for(turn.charger)
        slug = _slugify(turn.problem)
        uim = charger.charge(turn.problem, slug)
        path = self._runtime_root / "uims" / f"{turn.turn_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(uim, indent=2) + "\n")
        turn.uim_path = str(path)
        turn.notes.append(f"UIM charged ({turn.charger} charger, ok={uim.get('ok')})")

    def _stage_update_blueprint(self, turn: Turn) -> None:
        if not turn.blueprint_path or not turn.uim_path:
            turn.notes.append("update_blueprint skipped (no blueprint/uim)")
            return
        uim = json.loads(Path(turn.uim_path).read_text())
        phase = uim.get("phase1", {})
        with open(turn.blueprint_path, "a") as f:
            f.write(
                "\n## UIM (charge output)\n\n"
                f"- assumption: {phase.get('assumption', '—')}\n"
                f"- inversion: {phase.get('inversion', '—')}\n"
                f"- predictions: {len(phase.get('predictions', []))}\n"
            )

    def _stage_scan_papers(self, turn: Turn) -> None:
        uim: Dict[str, Any] = {}
        if turn.uim_path:
            try:
                uim = json.loads(Path(turn.uim_path).read_text())
            except Exception as exc:
                logger.warning("failed to load UIM %s: %s", turn.uim_path, exc)
        scanned = self._scanner.scan(turn.problem, uim)
        # Persist the full scan (matches + candidates) beside the other stage
        # artifacts — evidence the real feed ran, and input for the surface
        # stage that follows.
        path = self._runtime_root / "scans" / f"{turn.turn_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(scanned, indent=2) + "\n")
        turn.notes.append(f"scan: {scanned['papers_scanned']} papers ({scanned['notes']})")

    def _stage_surface_problems(self, turn: Turn) -> None:
        # Paper-derived candidates lead (from the persisted scan artifact);
        # fall back to the UIM's own predictions when there was no scan.
        candidates: List[str] = []
        scan_path = self._runtime_root / "scans" / f"{turn.turn_id}.json"
        if scan_path.exists():
            try:
                scan = json.loads(scan_path.read_text())
                candidates = [str(c) for c in (scan.get("candidates") or [])]
            except Exception as exc:
                logger.warning("failed to load scan artifact: %s", exc)
        if not candidates:
            uim: Dict[str, Any] = {}
            if turn.uim_path:
                try:
                    uim = json.loads(Path(turn.uim_path).read_text())
                except Exception as exc:
                    logger.warning("failed to load UIM %s: %s", turn.uim_path, exc)
            candidates = list((uim.get("phase1") or {}).get("predictions", []))[:3]
        turn.notes.append(f"next problems: {len(candidates)} candidate(s) surfaced")

    def _stage_build(self, turn: Turn) -> None:
        build_dir = self._runtime_root / "builds" / turn.turn_id
        build_dir.mkdir(parents=True, exist_ok=True)
        plan = build_dir / "build.md"
        plan.write_text(
            f"# Build Manifest — {turn.turn_id}\n\n"
            f"**Problem:** {turn.problem}\n"
            f"**Skill:** {turn.skill or '—'} (no skill wired -> manifest only)\n"
            f"**UIM:** {turn.uim_path or '—'}\n"
            f"**Approval:** {turn.approval_ids.get('build', '—')}\n"
        )
        turn.build_path = str(build_dir)
        if turn.skill:
            result = self._execute_skill(turn.skill)
            turn.notes.append(f"skill '{turn.skill}' executed: {result}")

    def _stage_combine(self, turn: Turn) -> None:
        other = self._newest_other_uim(turn)
        out = self._runtime_root / "combines" / f"{turn.turn_id}.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"# Cross-Domain Combine — {turn.turn_id}", "", f"**This turn:** {turn.problem}"]
        if other:
            other_uim = json.loads(Path(other).read_text())
            other_phase = other_uim.get("phase1", {})
            lines += [
                "", f"**Combined with:** {other_uim.get('slug', other.name)}",
                f"- their inversion: {other_phase.get('inversion', '—')}",
                f"- our inversion: {_uim_inversion_of(turn)}",
            ]
        else:
            lines += ["", "*No other research UIM found — combine recorded as single-domain.*"]
        out.write_text("\n".join(lines) + "\n")
        turn.combine_path = str(out)

    def _stage_record(self, turn: Turn) -> None:
        doc_dir = self._vault_root / "20_Research" / "flywheel"
        doc_dir.mkdir(parents=True, exist_ok=True)
        doc = doc_dir / f"{turn.turn_id}.md"
        doc.write_text(
            f"# Flywheel Turn {turn.turn_id}\n\n"
            f"- **Problem:** {turn.problem}\n"
            f"- **Status:** {turn.status}\n"
            f"- **Blueprint:** {turn.blueprint_path or '—'}\n"
            f"- **UIM:** {turn.uim_path or '—'}\n"
            f"- **Build:** {turn.build_path or '—'}\n"
            f"- **Combine:** {turn.combine_path or '—'}\n"
        )
        turn.record_path = str(doc)
        record = ArtifactRecord(
            artifact_id=f"flywheel/{turn.turn_id}",
            stage="flywheel",
            version="v1",
            payload={
                "problem": turn.problem,
                "uim_path": turn.uim_path,
                "blueprint_path": turn.blueprint_path,
                "record_path": str(doc),
            },
            profession="sovereign-operator",
        )
        self._axiom.publish(record)
        turn.notes.append("recorded to vault doc-trail + axiom library")

    # --- external reads (all contained) ------------------------------------

    def _vault_novelty(self, problem: str) -> float:
        """Semantic overlap with existing vault knowledge: the top /rag/search
        score for the problem statement. Offline/unavailable -> 0.0 (treated
        as novel — the approval gate, not this advisory check, is the
        load-bearing brake)."""
        try:
            with httpx.Client(timeout=4.0) as client:
                r = client.post(
                    f"http://127.0.0.1:{settings.port}/rag/search",
                    json={"tenant_id": "wilson-vault", "query": problem, "limit": 1},
                )
                if r.status_code == 200:
                    results = r.json().get("results", [])
                    if results:
                        return float(results[0].get("score", 0.0))
        except Exception as exc:
            logger.warning("rag relevance probe failed: %s", exc)
        return 0.0

    def _execute_skill(self, skill: str) -> str:
        try:
            with httpx.Client(timeout=10.0) as client:
                r = client.post(
                    f"http://127.0.0.1:{settings.port}/skills/execute",
                    json={"skill": skill},
                )
                if r.status_code == 200:
                    return "ok"
                return f"HTTP {r.status_code}"
        except Exception as exc:  # noqa: BLE001 — containment
            return f"unavailable: {type(exc).__name__}"

    def _newest_other_uim(self, turn: Turn) -> Optional[Path]:
        # Research UIMs live beside the flywheel runtime root — derive from
        # the injected root, never from settings (path-injection consistency).
        base = self._runtime_root.parent / "research"
        try:
            candidates = sorted(base.glob("**/*_UIM.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        except Exception:  # noqa: BLE001 — containment
            return None
        for p in candidates:
            if p.name != (turn.uim_path and Path(turn.uim_path).name or ""):
                return p
        return None


def _uim_inversion_of(turn: Turn) -> str:
    if not turn.uim_path:
        return "—"
    try:
        uim = json.loads(Path(turn.uim_path).read_text())
        return str((uim.get("phase1") or {}).get("inversion", "—"))
    except Exception:  # noqa: BLE001 — containment
        return "—"
