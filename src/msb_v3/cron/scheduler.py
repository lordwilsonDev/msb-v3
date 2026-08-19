"""The heartbeat — an in-process async scheduler for governed cron jobs.

``CronScheduler.run_loop()`` is the background task the FastAPI lifespan
starts (when MSB_CRON_ENABLED). Every tick it asks the store which jobs are
due and runs them. Every execution is governed exactly like the rest of the
runtime:

- kill-switch gate (fail-closed: an armed switch blocks the run, recorded
  as BLOCKED — never silently skipped),
- ``requires_approval`` jobs never auto-run on schedule (they are recorded
  as SKIPPED with a reason; only an operator-gated manual run executes them),
- overlap guard (a job with an in-flight run is never started twice),
- bounded retries + timeout (async timeout via ``asyncio.wait_for``),
- one evidence receipt per run on the JSONL audit stream + a chain record on
  the UAC AuditChain (best-effort mirror, same philosophy as tasks/lifecycle),
- run history in the derived projection store (cron_runs).

``run_job()`` is the reusable primitive the API's POST /cron/jobs/{id}/run
and the CLI's ``run`` share, so a manual run is governed identically to a
scheduled one.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from msb_v3.cron.actions import run_action
from msb_v3.cron.parser import CronExpr
from msb_v3.cron.store import CronStore
from msb_v3.observability.audit_log import append_receipt
from msb_v3.observability.metrics import CRON_RUNS

logger = logging.getLogger(__name__)

# A job whose execution exceeds this is treated as failed (asyncio.wait_for).
_DEFAULT_TIMEOUT_S = 300.0
_DEFAULT_MAX_RETRIES = 2


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CronScheduler:
    """Owns the store and executes jobs. Injectable for tests (a tmp-backed
    store and/or a stubbed action runner)."""

    def __init__(
        self,
        store: Optional[CronStore] = None,
        *,
        action_runner=run_action,
    ) -> None:
        self.store = store or CronStore()
        self._action_runner = action_runner

    # --- single job execution ---------------------------------------------

    async def run_job(self, job_id: str, trigger: str = "manual") -> Dict[str, Any]:
        """Run one job now (governed). Returns the run record. Used by the
        scheduler loop (trigger=\"schedule\"), the API, and the CLI."""
        try:
            job = self.store.get_job(job_id)
        except KeyError as exc:
            raise ValueError(str(exc)) from exc

        if self.store.is_running(job_id):
            return {
                "job_id": job_id,
                "status": "SKIPPED",
                "reason": "job already running (overlap guard)",
                "trigger": trigger,
            }

        # Kill-switch gate — fail-closed before any work starts.
        blocked_reason = self._killswitch_reason()
        if blocked_reason:
            return self._record_noop(job, trigger, "BLOCKED", f"kill switch: {blocked_reason}")

        # requires_approval: only an operator-gated manual run passes. The
        # schedule never auto-runs these — they are parked for the operator.
        if trigger == "schedule" and job["governance"].get("requires_approval"):
            return self._record_noop(job, trigger, "SKIPPED", "requires_approval — parked for operator")

        return await self._execute(job, trigger)

    async def _execute(self, job: Dict[str, Any], trigger: str) -> Dict[str, Any]:
        action = job["action"]
        action_type = action.get("type", "")
        params = action.get("params") or {}
        gov = job["governance"]
        max_retries = max(0, int(gov.get("max_retries", _DEFAULT_MAX_RETRIES)))
        timeout_s = float(gov.get("timeout_s", _DEFAULT_TIMEOUT_S))

        last_result: Dict[str, Any] = {"ok": False, "summary": "no attempt made", "detail": {}}
        attempts = 0
        for attempt in range(1, max_retries + 2):
            attempts = attempt
            run_id = self.store.start_run(job["job_id"], trigger, attempt=attempt)
            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(self._action_runner, action_type, params),
                    timeout=timeout_s,
                )
            except asyncio.TimeoutError:
                result = {"ok": False, "summary": f"timed out after {timeout_s}s", "detail": {}}
            except Exception as exc:  # noqa: BLE001 — runner bugs surface as failed runs
                result = {"ok": False, "summary": f"runner error: {exc.__class__.__name__}: {exc}", "detail": {}}
            if not isinstance(result, dict) or "ok" not in result:
                result = {"ok": False, "summary": f"action returned a malformed result: {result!r}", "detail": {}}
            last_result = result
            status = "SUCCESS" if result.get("ok") else "FAILED"
            error = None if result.get("ok") else str(result.get("summary"))
            self.store.finish_run(run_id, status, summary=result, error=error)
            self._emit_receipt(job, run_id, trigger, status, result, attempts)
            CRON_RUNS.labels(status=status).inc()
            if result.get("ok"):
                break
        self.store.prune_history(job["job_id"], keep=self._history_keep())
        return {
            "job_id": job["job_id"],
            "run_id": run_id,
            "status": last_result.get("ok") and "SUCCESS" or "FAILED",
            "attempts": attempts,
            "result": last_result,
            "trigger": trigger,
        }

    # --- internal helpers ---------------------------------------------------

    def _record_noop(self, job: Dict[str, Any], trigger: str, status: str, reason: str) -> Dict[str, Any]:
        """A run that never started (blocked/skipped) still lands on the
        audit stream + chain + history — a denial is evidence, not silence."""
        run_id = self.store.start_run(job["job_id"], trigger, attempt=1)
        self.store.finish_run(
            run_id,
            status,
            summary={"ok": False, "summary": reason, "detail": {}},
            error=reason,
        )
        self._emit_receipt(job, run_id, trigger, status, {"ok": False, "summary": reason, "detail": {}}, 1)
        CRON_RUNS.labels(status=status).inc()
        return {"job_id": job["job_id"], "run_id": run_id, "status": status, "reason": reason, "trigger": trigger}

    def _killswitch_reason(self) -> Optional[str]:
        try:
            from msb_v3.governance.killswitch import KillSwitch

            state = KillSwitch().state()
            if state.get("armed"):
                return state.get("reason") or "global kill switch armed"
        except Exception as exc:  # noqa: BLE001 — unreadable state is fail-closed
            return f"kill switch unreadable ({exc.__class__.__name__})"
        return None

    def _emit_receipt(
        self,
        job: Dict[str, Any],
        run_id: str,
        trigger: str,
        status: str,
        result: Dict[str, Any],
        attempts: int,
    ) -> None:
        """One evidence receipt per run: JSONL audit stream + UAC chain
        mirror. Best-effort — observability must never break the run."""
        receipt: Dict[str, Any] = {
            "event": "cron.run",
            "run_id": run_id,
            "job_id": job["job_id"],
            "trigger": trigger,
            "request": {
                "action": job["action"],
                "schedule": job["schedule"],
            },
            "status": status,
            "attempts": attempts,
            "timestamps": {"started_at": _now(), "finished_at": _now()},
            "result": result,
            "verification": {
                "method": "cron-run",
                # The action's result was produced by actually executing it
                # in-process against ground truth (db/chain/fs probes) — the
                # receipt is honest that this is what happened.
                "basis": "rerun",
            },
        }
        # Chain first, then the receipt carries the link to the authoritative
        # record (best-effort: a chain outage never breaks the run).
        try:
            from msb_ledger.chain_anchor import anchored_chain_from_env

            record = anchored_chain_from_env().append(
                "cron", f"cron.{status.lower()}", {"job_id": job["job_id"], "run_id": run_id, "status": status}
            )
            receipt["audit"] = {"seq": record.seq}
        except Exception as exc:  # noqa: BLE001
            logger.warning("cron chain mirror failed: %s", exc)
        try:
            append_receipt(receipt)
        except Exception as exc:  # noqa: BLE001
            logger.warning("cron receipt append failed: %s", exc)

    def _history_keep(self) -> int:
        from msb_v3.core.config import settings

        return max(1, int(settings.cron_history_keep))

    # --- the loop ----------------------------------------------------------

    def due_jobs(self, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """Jobs whose schedule fires at this minute AND that are enabled AND
        not currently running. Pure — the loop calls this each tick."""
        now = now or datetime.now(timezone.utc).replace(second=0, microsecond=0)
        due: List[Dict[str, Any]] = []
        for job in self.store.list_jobs():
            if not job["enabled"]:
                continue
            if self.store.is_running(job["job_id"]):
                continue
            try:
                if CronExpr.parse(job["schedule"]).matches(now):
                    due.append(job)
            except ValueError:
                logger.warning("cron job %s has an unparseable schedule; skipping", job["job_id"])
        return due

    async def tick(self) -> List[Dict[str, Any]]:
        """Run every due job once. Returns the per-job results."""
        results: List[Dict[str, Any]] = []
        for job in self.due_jobs():
            try:
                results.append(await self.run_job(job["job_id"], trigger="schedule"))
            except Exception as exc:  # noqa: BLE001 — one bad job never stops the loop
                logger.exception("cron job %s failed to run: %s", job["job_id"], exc)
        return results

    async def run_loop(self, stop: Optional[asyncio.Event] = None) -> None:
        """The heartbeat: wake every tick, run due jobs, recover in-flight
        runs once at startup, sleep between ticks. Runs until ``stop`` is
        set (or forever)."""
        from msb_v3.core.config import settings

        self.store.recover_inflight()
        tick_s = max(1, int(settings.cron_tick_s))
        logger.info("cron scheduler started (tick=%ss)", tick_s)
        while True:
            try:
                await self.tick()
            except Exception as exc:  # noqa: BLE001 — a broken tick must not kill the loop
                logger.exception("cron tick failed: %s", exc)
            if stop is not None:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=tick_s)
                    break
                except asyncio.TimeoutError:
                    continue
            else:
                await asyncio.sleep(tick_s)
        logger.info("cron scheduler stopped")
