# Background Windows — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A watch-only "Background" section in the desktop cockpit that shows what the runtime is doing behind the scenes (cron, governance, automation, wake, PLEI, recent governed runs), fed by a new read-only `GET /ops/background` snapshot.

**Architecture:** msb-v3 gains `src/msb_v3/ops/background.py`. For each subsystem it has a *reader* (fetch raw status in-process) and a *pure classifier* (raw → `{state, detail, error}`). A router `src/msb_v3/api/ops_background.py` serves the snapshot behind the operator token, with a 3 s cache. The Electron cockpit gets 5 new named bridge methods, a small visible-only poller (`renderer/poller.js`, unit-tested in Node), and a `renderer/background.js` view module mounted as a new "Background" tab.

**Tech Stack:** Python 3 / FastAPI / pytest (msb-v3), Electron 35 vanilla JS / `node --test` (desktop).

**Spec:** `docs/superpowers/specs/2026-09-22-background-windows-design.md`. This plan covers **Phase 1** only; launchd (phase 2) and live streaming / Guardian (phase 3) are out of scope.

## Global Constraints

- Watch only: no route, bridge method or UI control added here may change state.
- The cockpit never touches the machine directly: no `child_process`, no `fs` in `desktop/src/main/*`, no network in the renderer (enforced by `desktop/test/security.test.js`).
- States are exactly `ok`, `warn`, `fail`, `unknown`. Data that can't be read is `unknown`, never `ok`.
- A subsystem reader that raises makes **only that subsystem** `unknown`; the snapshot still returns the others.
- `/ops/background` requires the operator token (`Depends(require_operator)`).
- Snapshot cache TTL: 3 s. Poll intervals: Overview 5 s, other views 10 s, 30 s back-off after a failed read. Polling pauses while the document is hidden.
- Budget warning threshold: spent / limit **> 0.8** (both the governance budgets and the automation USD cap).
- Every new renderer string from the server goes through `textContent` (the existing `el()` helper), never `innerHTML` with interpolation.

## Environment notes (read first)

- Work in the worktree `~/projects/AI-Agents/msb-v3-bg-spec` (branch `docs/background-windows-spec`). Other sessions have uncommitted work in the main checkout; never touch it.
- The package is **editable-installed from the main checkout**. In the worktree, always run Python tests with `PYTHONPATH=$PWD/src`, or they import the main checkout's code:
  `PYTHONPATH=$PWD/src /opt/homebrew/Caskroom/miniforge/base/bin/python -m pytest ...`
  Below, `PY` means that full prefix.
- Desktop tests: `cd desktop && npm test` (runs `node --test test/*.test.js`). `node_modules` exists in the main checkout's `desktop/` only; if `npm test` complains about missing modules, run `npm ci` inside the worktree's `desktop/` first. The tests use only `node:` built-ins.
- `tests/conftest.py` already redirects the cron, wake, automation and governance DB paths to `tmp_path` for every test (autouse fixtures). The governance router's **module-level singletons** (`_switch`, `_ledger`, `_queue`, `_governor`) are created at import time and are *not* redirected, so these tests never call the real governance reader against live state; they inject a fake instead.
- Branch `fix/plei-calibration-test-isolation` (not merged yet) redirects the PLEI calibration store in tests. This plan does not depend on it: the PLEI reader test passes an explicit tmp store.

## File map

| File | Status | Responsibility |
| --- | --- | --- |
| `src/msb_v3/ops/background.py` | create | `Entry`, `parse_ts`, `missed_fire`, 5 classifiers, 5 readers, `build_snapshot`, `SnapshotCache` |
| `src/msb_v3/api/ops_background.py` | create | `GET /ops/background` router |
| `src/msb_v3/api/app.py` | modify | import + mount the router at `/ops` |
| `docs/SURFACE.md` | modify | classify the new router (enforced by `tests/docs/test_surface_map.py`) |
| `tests/ops/test_background_snapshot.py` | create | classifiers, readers, snapshot, cache |
| `tests/ops/test_background_api.py` | create | route auth + shape |
| `desktop/src/main/bridge.js` | modify | `background`, `cronJobs`, `cronHistory`, `pleiCalibrate`, `auditStream` |
| `desktop/src/main/validate.js` | modify | validators for `cronHistory`, `auditStream` |
| `desktop/src/main/index.js` | modify | register 5 channels |
| `desktop/src/preload/index.js` | modify | expose 5 methods |
| `desktop/src/renderer/poller.js` | create | visible-only poller with back-off (UMD: browser global + Node export) |
| `desktop/src/renderer/background.js` | create | the 6 phase-1 views, mount/unmount |
| `desktop/src/renderer/app.js` | modify | "Background" tab; mount/unmount the section |
| `desktop/src/renderer/index.html` | modify | load `poller.js`, `background.js`; state badge CSS |
| `desktop/test/bridge.test.js`, `validate.test.js`, `security.test.js` | modify | new methods, validators, surface |
| `desktop/test/poller.test.js` | create | poller behaviour |
| `docs/superpowers/specs/2026-09-22-background-windows-design.md` | modify | record the phase-1 adjustments found while planning |

---

### Task 1: Spec adjustments found while planning

Three facts found in the code change the spec slightly. Record them before building so the spec stays the source of truth.

**Files:**
- Modify: `docs/superpowers/specs/2026-09-22-background-windows-design.md`

- [x] **Step 1: Edit the spec**

In the **Window map** table, replace the Activity and PLEI rows with:

```markdown
| 2 | Activity | Phase 1: latest governed-run receipts (newest first). Phase 3: the full audit-chain feed, including cron. There is no read route for the hash chain today | `GET /cockpit/audit` (phase 1) |
| 7 | PLEI | Prediction / outcome / pair counts, chain integrity, last prediction time, and the calibration report | `plei` in `/ops/background`, `GET /plei/calibrate` |
```

In the **Cockpit side → Bridge** table, replace the `pleiStatus()` row and add `auditStream`:

```markdown
| `pleiCalibrate()` | no args |
| `auditStream(limit)` | `limit` integer 1–500, default 50 |
```

and change the sentence under it to: `Activity uses auditStream(); Governance reuses governanceStatus() and approvals().`

In **Phases**, replace item 1 with:

```markdown
1. `/ops/background` with subsystems only; Overview, Activity (governed-run receipts), Cron, Governance, Automation & wake, PLEI views; bridge methods except `launchdLog`.
```

Add a new section at the end of the spec, before **Non-goals**:

```markdown
## Adjustments made while planning (2026-09-22)

- `GET /plei/status` runs a full project ingest (`ingest_all`) on every call, which is too heavy for 10 s polling. The PLEI view uses the cheap `plei` snapshot entry plus `GET /plei/calibrate` (reads the store only).
- `GET /cockpit/audit` returns governed-run receipts (`logs/audit.jsonl`), not the hash-chained audit log that cron writes to. Phase 1 Activity shows receipts. A chain read route and the merged feed move to phase 3.
- `bridge.cockpit()` calls `/cockpit/api`, not `/cockpit/audit`, so Activity needs its own `auditStream()` method.
```

- [x] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-09-22-background-windows-design.md
git commit -m "docs(spec): background windows - phase-1 adjustments from planning"
```

---

### Task 2: Snapshot core: `Entry`, time helpers, cron classifier

**Files:**
- Create: `src/msb_v3/ops/background.py`
- Test: `tests/ops/test_background_snapshot.py`

**Interfaces:**
- Produces:
  - `OK, WARN, FAIL, UNKNOWN: str` (`"ok"`, `"warn"`, `"fail"`, `"unknown"`)
  - `BUDGET_WARN_RATIO = 0.8`, `CACHE_TTL_S = 3.0`
  - `@dataclass Entry(state: str, detail: dict = {}, error: Optional[str] = None)` with `.as_dict() -> dict`
  - `parse_ts(value: Any) -> Optional[datetime]` (aware UTC, or None)
  - `missed_fire(schedule: str, since: datetime, now: datetime, grace: timedelta) -> bool`
  - `classify_cron(raw: dict, now: datetime) -> Entry`, where raw is `{"enabled": bool, "tick_s": int, "jobs": [{"job_id": str, "schedule": str, "enabled": bool, "last_run": {"status": str, "started_at": str} | None}]}`
  - cron `detail` keys: `scheduler_enabled`, `job_count`, `failed` (list of job_ids), `overdue` (list of job_ids)

- [x] **Step 1: Write the failing tests**

Create `tests/ops/test_background_snapshot.py`:

```python
"""Tests for the /ops/background snapshot (ops/background.py).

Classifiers are pure (raw dict + now -> Entry), so most tests need no DB.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from msb_v3.ops.background import (
    FAIL,
    OK,
    UNKNOWN,
    WARN,
    Entry,
    classify_cron,
    missed_fire,
    parse_ts,
)

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)


def _job(job_id="j", schedule="*/5 * * * *", enabled=True, status="SUCCESS", started=NOW - timedelta(minutes=2)):
    last = None if status is None else {"status": status, "started_at": started.isoformat()}
    return {"job_id": job_id, "schedule": schedule, "enabled": enabled, "last_run": last}


# --- helpers -------------------------------------------------------------

def test_parse_ts_handles_z_naive_and_garbage():
    assert parse_ts("2026-09-22T12:00:00Z") == NOW
    assert parse_ts("2026-09-22T12:00:00") == NOW  # naive -> UTC
    assert parse_ts("not a time") is None
    assert parse_ts(None) is None
    assert parse_ts("") is None


def test_missed_fire_true_only_when_a_due_fire_plus_grace_has_passed():
    grace = timedelta(minutes=2)
    # every 5 min; last ran 12 min ago -> the fire 7 min ago was missed
    assert missed_fire("*/5 * * * *", NOW - timedelta(minutes=12), NOW, grace) is True
    # last ran 2 min ago -> next fire is in the future
    assert missed_fire("*/5 * * * *", NOW - timedelta(minutes=2), NOW, grace) is False
    # the 11:55 fire was missed, but a 6 min grace still covers it
    assert missed_fire("*/5 * * * *", NOW - timedelta(minutes=6), NOW, timedelta(minutes=6)) is False
    # same fire with the 2 min grace -> missed
    assert missed_fire("*/5 * * * *", NOW - timedelta(minutes=6), NOW, grace) is True


def test_entry_as_dict():
    assert Entry(OK, {"a": 1}).as_dict() == {"state": "ok", "detail": {"a": 1}, "error": None}


# --- cron ----------------------------------------------------------------

def test_cron_ok_when_enabled_and_recent_success():
    e = classify_cron({"enabled": True, "tick_s": 30, "jobs": [_job()]}, NOW)
    assert e.state == OK
    assert e.detail == {"scheduler_enabled": True, "job_count": 1, "failed": [], "overdue": []}


def test_cron_fail_when_latest_run_of_enabled_job_failed():
    e = classify_cron({"enabled": True, "tick_s": 30, "jobs": [_job(status="FAILED")]}, NOW)
    assert e.state == FAIL
    assert e.detail["failed"] == ["j"]


def test_cron_failed_disabled_job_is_ignored():
    e = classify_cron({"enabled": True, "tick_s": 30, "jobs": [_job(enabled=False, status="FAILED")]}, NOW)
    assert e.state == OK


def test_cron_warn_when_overdue():
    job = _job(started=NOW - timedelta(minutes=30))
    e = classify_cron({"enabled": True, "tick_s": 30, "jobs": [job]}, NOW)
    assert e.state == WARN
    assert e.detail["overdue"] == ["j"]


def test_cron_never_run_job_is_not_overdue():
    e = classify_cron({"enabled": True, "tick_s": 30, "jobs": [_job(status=None)]}, NOW)
    assert e.state == OK


def test_cron_warn_when_scheduler_disabled():
    e = classify_cron({"enabled": False, "tick_s": 30, "jobs": [_job()]}, NOW)
    assert e.state == WARN
    assert e.detail["scheduler_enabled"] is False


def test_cron_bad_schedule_does_not_blank_the_entry():
    e = classify_cron({"enabled": True, "tick_s": 30, "jobs": [_job(schedule="nonsense")]}, NOW)
    assert e.state == OK
    assert e.detail["overdue"] == []
```

- [x] **Step 2: Run to verify it fails**

Run: `PY -m pytest -q tests/ops/test_background_snapshot.py`
Expected: FAIL / collection error `ModuleNotFoundError: No module named 'msb_v3.ops.background'`.

- [x] **Step 3: Implement**

Create `src/msb_v3/ops/background.py`:

```python
"""Background snapshot: one read-only view of what runs behind the runtime.

Feeds ``GET /ops/background`` (the desktop cockpit's Background section;
docs/superpowers/specs/2026-09-22-background-windows-design.md). Each
subsystem is a reader (fetch raw status in-process) plus a pure classifier
(raw -> Entry). A reader that raises yields an ``unknown`` entry for that
subsystem only; the others still report. Nothing here writes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from msb_v3.cron.parser import CronExpr

OK, WARN, FAIL, UNKNOWN = "ok", "warn", "fail", "unknown"
BUDGET_WARN_RATIO = 0.8
CACHE_TTL_S = 3.0

Raw = Dict[str, Any]


@dataclass
class Entry:
    """One subsystem's state as the cockpit shows it."""

    state: str
    detail: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def parse_ts(value: Any) -> Optional[datetime]:
    """ISO-8601 string -> aware UTC datetime; anything else -> None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def missed_fire(schedule: str, since: datetime, now: datetime, grace: timedelta) -> bool:
    """True when ``schedule`` was due to fire after ``since`` and that fire
    time plus ``grace`` has already passed, i.e. a run was missed."""
    nxt = CronExpr.parse(schedule).next_after(since)
    return nxt is not None and nxt + grace < now


# --- classifiers (pure) ----------------------------------------------------


def classify_cron(raw: Raw, now: datetime) -> Entry:
    """fail: an enabled job's latest run FAILED. warn: an enabled job missed
    a fire, or the scheduler itself is off. Jobs that never ran are not
    judged overdue (there is no baseline)."""
    grace = timedelta(seconds=2 * int(raw.get("tick_s") or 60))
    jobs = raw.get("jobs") or []
    failed: List[str] = []
    overdue: List[str] = []
    for job in jobs:
        if not job.get("enabled"):
            continue
        last = job.get("last_run") or {}
        if last.get("status") == "FAILED":
            failed.append(job["job_id"])
        started = parse_ts(last.get("started_at"))
        if started is None:
            continue
        try:
            if missed_fire(job["schedule"], started, now, grace):
                overdue.append(job["job_id"])
        except ValueError:
            continue  # unparseable schedule: the cron window shows the raw string
    detail = {
        "scheduler_enabled": bool(raw.get("enabled")),
        "job_count": len(jobs),
        "failed": failed,
        "overdue": overdue,
    }
    if failed:
        return Entry(FAIL, detail)
    if overdue or not raw.get("enabled"):
        return Entry(WARN, detail)
    return Entry(OK, detail)
```

- [x] **Step 4: Run to verify it passes**

Run: `PY -m pytest -q tests/ops/test_background_snapshot.py`
Expected: 10 passed.

- [x] **Step 5: Commit**

```bash
git add src/msb_v3/ops/background.py tests/ops/test_background_snapshot.py
git commit -m "feat(ops): background snapshot core + cron classifier"
```

---

### Task 3: Governance, automation, wake and PLEI classifiers

**Files:**
- Modify: `src/msb_v3/ops/background.py` (append after `classify_cron`)
- Test: `tests/ops/test_background_snapshot.py` (append)

**Interfaces:**
- Consumes: `Entry`, `parse_ts`, `missed_fire`, the state constants, `BUDGET_WARN_RATIO` (Task 2)
- Produces:
  - `classify_governance(raw, now) -> Entry`. raw is the dict returned by `msb_v3.api.governance.status()`: `{"killswitch": {"armed": bool, "fail_closed"?: bool, "reason"?: str, "scopes"?: [dict]}, "budgets": {cat: {"spent": num, "limit": num}}, "approvals": {"pending": int}}`. detail keys: `killswitch_armed`, `fail_closed`, `armed_scopes`, `scope_read_error`, `budgets_over_80pct`, `pending_approvals`.
  - `classify_automation(raw, now) -> Entry`. raw is `automation_status()`: `{"dry_run": bool, "budget": {"cap_usd", "spent_usd", "remaining_usd"}, "providers": {name: {"configured": bool}}}`. detail keys: `dry_run`, `cap_usd`, `spent_usd`, `remaining_usd`, `providers_configured`, `providers_total`.
  - `classify_wake(raw, now) -> Entry`. raw is `wake_status()` plus `oldest_pending_ts`. detail keys: `enabled`, `schedule`, `pending`, `outbox_count`, `job_present`, `oldest_pending_ts`.
  - `classify_plei(raw, now) -> Entry`. raw is `{"predictions", "outcomes", "pairs", "chain_ok", "chain_message", "last_forecast_at"}`; detail is the same dict.

- [x] **Step 1: Write the failing tests**

Append to `tests/ops/test_background_snapshot.py` (and add `classify_automation, classify_governance, classify_plei, classify_wake` to the import list at the top):

```python
# --- governance ------------------------------------------------------------

def _gov(armed=False, scopes=None, budgets=None, pending=0, **ks):
    return {
        "killswitch": {"armed": armed, "scopes": scopes or [], **ks},
        "budgets": budgets or {"llm": {"spent": 1, "limit": 100}},
        "approvals": {"pending": pending},
    }


def test_governance_ok():
    e = classify_governance(_gov(pending=2), NOW)
    assert e.state == OK
    assert e.detail["pending_approvals"] == 2
    assert e.detail["killswitch_armed"] is False


def test_governance_fail_when_killswitch_armed():
    assert classify_governance(_gov(armed=True), NOW).state == FAIL


def test_governance_fail_closed_state_is_fail():
    e = classify_governance(_gov(armed=True, fail_closed=True), NOW)
    assert e.state == FAIL
    assert e.detail["fail_closed"] is True


def test_governance_warn_on_armed_scope():
    e = classify_governance(_gov(scopes=[{"scope_type": "tool", "scope_id": "vault_write"}]), NOW)
    assert e.state == WARN
    assert e.detail["armed_scopes"] == 1


def test_governance_warn_when_scope_list_unreadable():
    e = classify_governance(_gov(scopes=[{"error": "db locked"}]), NOW)
    assert e.state == WARN
    assert e.detail["scope_read_error"] is True


def test_governance_warn_when_budget_over_80pct_and_unlimited_ignored():
    budgets = {"llm": {"spent": 81, "limit": 100}, "tools": {"spent": 999, "limit": -1}}
    e = classify_governance(_gov(budgets=budgets), NOW)
    assert e.state == WARN
    assert e.detail["budgets_over_80pct"] == ["llm"]


def test_governance_unknown_without_killswitch_block():
    assert classify_governance({"budgets": {}}, NOW).state == UNKNOWN


# --- automation ------------------------------------------------------------

def _auto(spent=1.0, cap=10.0, dry_run=True):
    return {
        "dry_run": dry_run,
        "budget": {"cap_usd": cap, "spent_usd": spent, "remaining_usd": cap - spent},
        "providers": {"n8n": {"configured": True}, "make": {"configured": False}},
    }


def test_automation_ok_with_detail():
    e = classify_automation(_auto(), NOW)
    assert e.state == OK
    assert e.detail["dry_run"] is True
    assert e.detail["providers_configured"] == 1
    assert e.detail["providers_total"] == 2


def test_automation_warn_over_80pct_of_cap():
    assert classify_automation(_auto(spent=8.5, cap=10.0), NOW).state == WARN


def test_automation_zero_cap_is_not_a_warning():
    assert classify_automation(_auto(spent=0.0, cap=0.0), NOW).state == OK


# --- wake ------------------------------------------------------------------

def _wake(enabled=True, pending=0, job_present=True, oldest=None):
    return {
        "enabled": enabled,
        "schedule": "*/5 * * * *",
        "pending": pending,
        "outbox_count": 3,
        "job_present": job_present,
        "oldest_pending_ts": oldest,
    }


def test_wake_ok_when_idle():
    e = classify_wake(_wake(), NOW)
    assert e.state == OK
    assert e.detail["outbox_count"] == 3


def test_wake_fail_when_enabled_but_no_cron_job():
    assert classify_wake(_wake(job_present=False), NOW).state == FAIL


def test_wake_disabled_is_ok_not_fail():
    assert classify_wake(_wake(enabled=False, job_present=False), NOW).state == OK


def test_wake_warn_when_oldest_pending_missed_a_cycle():
    old = (NOW - timedelta(minutes=20)).isoformat()
    assert classify_wake(_wake(pending=1, oldest=old), NOW).state == WARN


def test_wake_recent_pending_is_ok():
    recent = (NOW - timedelta(minutes=1)).isoformat()
    assert classify_wake(_wake(pending=1, oldest=recent), NOW).state == OK


# --- plei --------------------------------------------------------------------

def _plei(chain_ok=True):
    return {
        "predictions": 4,
        "outcomes": 2,
        "pairs": 2,
        "chain_ok": chain_ok,
        "chain_message": "ok" if chain_ok else "hash mismatch at 3",
        "last_forecast_at": "2026-09-22T11:00:00Z",
    }


def test_plei_ok():
    e = classify_plei(_plei(), NOW)
    assert e.state == OK
    assert e.detail["pairs"] == 2


def test_plei_fail_on_broken_chain():
    assert classify_plei(_plei(chain_ok=False), NOW).state == FAIL
```

- [x] **Step 2: Run to verify they fail**

Run: `PY -m pytest -q tests/ops/test_background_snapshot.py`
Expected: ImportError for `classify_governance`.

- [x] **Step 3: Implement**

Append to `src/msb_v3/ops/background.py`:

```python
def _over_ratio(spent: Any, limit: Any) -> bool:
    """spent/limit > BUDGET_WARN_RATIO; limits <= 0 mean unlimited/unset."""
    if not isinstance(limit, (int, float)) or not isinstance(spent, (int, float)) or limit <= 0:
        return False
    return spent / limit > BUDGET_WARN_RATIO


def classify_governance(raw: Raw, now: datetime) -> Entry:
    """fail: kill switch armed (including fail-closed on unreadable state).
    warn: a scoped lockdown is armed, the scope list is unreadable, or a
    budget is over 80%."""
    if "killswitch" not in raw:
        return Entry(UNKNOWN, {}, "no kill-switch state in governance status")
    ks = raw.get("killswitch") or {}
    scope_rows = ks.get("scopes") or []
    scopes = [s for s in scope_rows if "error" not in s]
    scope_error = len(scopes) != len(scope_rows)
    hot = [cat for cat, b in (raw.get("budgets") or {}).items() if _over_ratio(b.get("spent"), b.get("limit"))]
    detail = {
        "killswitch_armed": bool(ks.get("armed")),
        "fail_closed": bool(ks.get("fail_closed")),
        "armed_scopes": len(scopes),
        "scope_read_error": scope_error,
        "budgets_over_80pct": hot,
        "pending_approvals": int((raw.get("approvals") or {}).get("pending", 0)),
    }
    if ks.get("armed"):
        return Entry(FAIL, detail)
    if scopes or scope_error or hot:
        return Entry(WARN, detail)
    return Entry(OK, detail)


def classify_automation(raw: Raw, now: datetime) -> Entry:
    """warn: spend is over 80% of the USD cap. Dry-run is reported, not judged."""
    budget = raw.get("budget") or {}
    providers = raw.get("providers") or {}
    detail = {
        "dry_run": bool(raw.get("dry_run")),
        "cap_usd": budget.get("cap_usd"),
        "spent_usd": budget.get("spent_usd"),
        "remaining_usd": budget.get("remaining_usd"),
        "providers_configured": sum(1 for p in providers.values() if p.get("configured")),
        "providers_total": len(providers),
    }
    if _over_ratio(budget.get("spent_usd"), budget.get("cap_usd")):
        return Entry(WARN, detail)
    return Entry(OK, detail)


def classify_wake(raw: Raw, now: datetime) -> Entry:
    """fail: wake is enabled but its cron job is missing (nothing will answer).
    warn: the oldest pending message has sat through a missed wake cycle.
    Disabled wake is a configuration choice, reported as ok."""
    detail = {
        "enabled": bool(raw.get("enabled")),
        "schedule": raw.get("schedule"),
        "pending": int(raw.get("pending") or 0),
        "outbox_count": raw.get("outbox_count"),
        "job_present": bool(raw.get("job_present")),
        "oldest_pending_ts": raw.get("oldest_pending_ts"),
    }
    if not detail["enabled"]:
        return Entry(OK, detail)
    if not detail["job_present"]:
        return Entry(FAIL, detail)
    oldest = parse_ts(raw.get("oldest_pending_ts"))
    if detail["pending"] and oldest is not None and isinstance(raw.get("schedule"), str):
        try:
            if missed_fire(raw["schedule"], oldest, now, timedelta(minutes=2)):
                return Entry(WARN, detail)
        except ValueError:
            pass
    return Entry(OK, detail)


def classify_plei(raw: Raw, now: datetime) -> Entry:
    """fail: the calibration hash chain does not verify."""
    detail = dict(raw)
    return Entry(OK if raw.get("chain_ok") else FAIL, detail)
```

- [x] **Step 4: Run to verify they pass**

Run: `PY -m pytest -q tests/ops/test_background_snapshot.py`
Expected: 27 passed.

- [x] **Step 5: Commit**

```bash
git add src/msb_v3/ops/background.py tests/ops/test_background_snapshot.py
git commit -m "feat(ops): governance, automation, wake, plei classifiers"
```

---

### Task 4: Readers, `build_snapshot` and the cache

**Files:**
- Modify: `src/msb_v3/ops/background.py` (append)
- Test: `tests/ops/test_background_snapshot.py` (append)

**Interfaces:**
- Consumes: all classifiers (Tasks 2–3)
- Produces:
  - `async def read_cron() -> Raw`, `read_governance()`, `read_automation()`, `read_wake()`, `read_plei(store=None)`
  - `READERS: Dict[str, Tuple[Callable[[], Awaitable[Raw]], Callable[[Raw, datetime], Entry]]]` with keys in this order: `cron`, `governance`, `automation`, `wake`, `plei`
  - `async def build_snapshot(readers=None, now=None) -> {"generated_at": str, "subsystems": {name: Entry.as_dict()}}`
  - `class SnapshotCache(ttl_s=CACHE_TTL_S, clock=time.monotonic)` with `async def get(self, build) -> dict`

- [x] **Step 1: Write the failing tests**

Append to `tests/ops/test_background_snapshot.py` (add `asyncio` to the imports, plus `READERS, SnapshotCache, build_snapshot, read_cron, read_plei, read_wake` from `msb_v3.ops.background`):

```python
# --- readers (real stores; conftest redirects their DBs to tmp_path) --------

def test_read_cron_reports_jobs_with_last_run():
    from msb_v3.cron.store import CronStore

    store = CronStore()
    store.create_job("hb", "Heartbeat", "*/5 * * * *", {"type": "health_check", "params": {}}, {})
    run_id = store.start_run("hb", "manual")
    store.finish_run(run_id, "SUCCESS", {})
    raw = asyncio.run(read_cron())
    assert [j["job_id"] for j in raw["jobs"]] == ["hb"]
    assert raw["jobs"][0]["last_run"]["status"] == "SUCCESS"
    assert "tick_s" in raw and "enabled" in raw


def test_read_wake_adds_oldest_pending_ts():
    from msb_v3.wake.store import WakeStore

    WakeStore().post("hello", sender="test")
    raw = asyncio.run(read_wake())
    assert raw["pending"] == 1
    assert raw["oldest_pending_ts"]


def test_read_plei_from_explicit_store(tmp_path):
    from msb_v3.plei.calibration.store import CalibrationStore

    raw = asyncio.run(read_plei(store=CalibrationStore(path=tmp_path / "cal.jsonl")))
    assert raw == {
        "predictions": 0,
        "outcomes": 0,
        "pairs": 0,
        "chain_ok": True,
        "chain_message": "empty chain",
        "last_forecast_at": None,
    }


# --- snapshot ----------------------------------------------------------------

def test_readers_cover_the_five_subsystems_in_order():
    assert list(READERS) == ["cron", "governance", "automation", "wake", "plei"]


def test_build_snapshot_isolates_a_failing_reader():
    async def good():
        return {"predictions": 1, "outcomes": 0, "pairs": 0, "chain_ok": True,
                "chain_message": "ok", "last_forecast_at": None}

    async def broken():
        raise RuntimeError("db locked")

    snap = asyncio.run(build_snapshot(
        readers={"plei": (good, classify_plei), "cron": (broken, classify_cron)}, now=NOW,
    ))
    assert snap["generated_at"] == NOW.isoformat()
    assert snap["subsystems"]["plei"]["state"] == OK
    assert snap["subsystems"]["cron"] == {"state": UNKNOWN, "detail": {}, "error": "RuntimeError: db locked"}


def test_build_snapshot_isolates_a_failing_classifier():
    async def read():
        return {"jobs": [{"job_id": "x"}]}  # missing keys -> classifier raises

    snap = asyncio.run(build_snapshot(readers={"cron": (read, classify_cron)}, now=NOW))
    assert snap["subsystems"]["cron"]["state"] == UNKNOWN


def test_cache_reuses_within_ttl_and_rebuilds_after():
    t = [100.0]
    calls = []

    async def build():
        calls.append(t[0])
        return {"n": len(calls)}

    cache = SnapshotCache(ttl_s=3.0, clock=lambda: t[0])
    assert asyncio.run(cache.get(build)) == {"n": 1}
    t[0] = 102.0
    assert asyncio.run(cache.get(build)) == {"n": 1}
    t[0] = 103.5
    assert asyncio.run(cache.get(build)) == {"n": 2}
```

Before running, confirm the `CronStore` and `WakeStore` call signatures used above match the real code (`grep -n "def create_job\|def start_run\|def finish_run" -A6 src/msb_v3/cron/store.py` and `grep -n "def post" -A3 src/msb_v3/wake/store.py`). If a positional order differs, fix **the test** to match the store; do not change the stores.

- [x] **Step 2: Run to verify they fail**

Run: `PY -m pytest -q tests/ops/test_background_snapshot.py`
Expected: ImportError for `READERS`.

- [x] **Step 3: Implement**

Add `import time` at the top of `src/msb_v3/ops/background.py`, extend the typing import to `from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple`, then append:

```python
# --- readers (in-process; never HTTP self-calls) ---------------------------
#
# They reuse the existing status route functions where one exists, so the
# snapshot and the per-subsystem routes can never disagree about the numbers.


async def read_cron() -> Raw:
    from msb_v3.core.config import settings
    from msb_v3.cron.store import CronStore

    store = CronStore()
    jobs = []
    for job in store.list_jobs():
        latest = store.history(job["job_id"], limit=1)
        jobs.append(
            {
                "job_id": job["job_id"],
                "schedule": job["schedule"],
                "enabled": bool(job["enabled"]),
                "last_run": (
                    {"status": latest[0]["status"], "started_at": latest[0]["started_at"]} if latest else None
                ),
            }
        )
    return {"enabled": bool(settings.cron_enabled), "tick_s": int(settings.cron_tick_s), "jobs": jobs}


async def read_governance() -> Raw:
    # The governance singletons (kill switch, ledger, queue) live in the
    # router module; its status() is the one fail-closed reading of them.
    from msb_v3.api import governance as governance_api

    return await governance_api.status()


async def read_automation() -> Raw:
    from msb_v3.api.automation import automation_status

    return automation_status()


async def read_wake() -> Raw:
    from msb_v3.api.wake import wake_status
    from msb_v3.wake.store import WakeStore

    raw = dict(wake_status())
    oldest = WakeStore().pending(limit=1)
    raw["oldest_pending_ts"] = oldest[0]["ts"] if oldest else None
    return raw


async def read_plei(store: Any = None) -> Raw:
    from msb_v3.plei.calibration.store import CalibrationStore

    store = store or CalibrationStore()
    predictions = store.predictions()
    chain_ok, chain_message = store.verify_chain()
    return {
        "predictions": len(predictions),
        "outcomes": store.outcome_count(),
        "pairs": store.pair_count(),
        "chain_ok": chain_ok,
        "chain_message": chain_message,
        "last_forecast_at": predictions[-1].forecast_at if predictions else None,
    }


Reader = Callable[[], Awaitable[Raw]]
Classifier = Callable[[Raw, datetime], Entry]

READERS: Dict[str, Tuple[Reader, Classifier]] = {
    "cron": (read_cron, classify_cron),
    "governance": (read_governance, classify_governance),
    "automation": (read_automation, classify_automation),
    "wake": (read_wake, classify_wake),
    "plei": (read_plei, classify_plei),
}


async def build_snapshot(
    readers: Optional[Dict[str, Tuple[Reader, Classifier]]] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Read and classify every subsystem. One broken subsystem becomes
    ``unknown`` with its error; it never blanks the others."""
    readers = READERS if readers is None else readers
    now = now or datetime.now(timezone.utc)
    subsystems: Dict[str, Any] = {}
    for name, (read, classify) in readers.items():
        try:
            entry = classify(await read(), now)
        except Exception as exc:  # noqa: BLE001 - isolation is the point
            entry = Entry(UNKNOWN, {}, f"{type(exc).__name__}: {exc}")
        subsystems[name] = entry.as_dict()
    return {"generated_at": now.isoformat(), "subsystems": subsystems}


class SnapshotCache:
    """Serve one snapshot for ``ttl_s`` so several polling windows share it.
    Two concurrent misses may both build; that is harmless (reads only)."""

    def __init__(self, ttl_s: float = CACHE_TTL_S, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl_s
        self._clock = clock
        self._value: Optional[Dict[str, Any]] = None
        self._at = 0.0

    async def get(self, build: Callable[[], Awaitable[Dict[str, Any]]]) -> Dict[str, Any]:
        now = self._clock()
        if self._value is None or now - self._at >= self._ttl:
            self._value = await build()
            self._at = now
        return self._value
```

- [x] **Step 4: Run to verify they pass**

Run: `PY -m pytest -q tests/ops/test_background_snapshot.py`
Expected: 34 passed.

- [x] **Step 5: Commit**

```bash
git add src/msb_v3/ops/background.py tests/ops/test_background_snapshot.py
git commit -m "feat(ops): background readers, snapshot builder, 3s cache"
```

---

### Task 5: `GET /ops/background` route, mounted and classified

**Files:**
- Create: `src/msb_v3/api/ops_background.py`
- Modify: `src/msb_v3/api/app.py` (imports near line 16–51; mounts near line 241–248)
- Modify: `docs/SURFACE.md` (router table)
- Test: `tests/ops/test_background_api.py`

**Interfaces:**
- Consumes: `build_snapshot`, `SnapshotCache` (Task 4)
- Produces: `GET /ops/background` → the `build_snapshot()` dict; 503 when no operator token is configured, 401 on a wrong token.

- [x] **Step 1: Write the failing test**

Create `tests/ops/test_background_api.py`:

```python
"""GET /ops/background: operator-gated, read-only snapshot route."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from msb_v3.api import ops_background
from msb_v3.api.app import create_app
from msb_v3.core.config import settings
from msb_v3.ops.background import SnapshotCache


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("MSB_OPERATOR_TOKEN", "tok")
    monkeypatch.setattr(settings, "operator_token", "tok")
    monkeypatch.setattr(ops_background, "_cache", SnapshotCache(ttl_s=0))

    async def fake_build():
        return {"generated_at": "2026-09-22T12:00:00+00:00",
                "subsystems": {"cron": {"state": "ok", "detail": {}, "error": None}}}

    monkeypatch.setattr(ops_background, "build_snapshot", fake_build)
    return TestClient(create_app())


def test_requires_operator_token(client: TestClient) -> None:
    assert client.get("/ops/background").status_code == 401
    assert client.get("/ops/background", headers=_auth("wrong")).status_code == 401


def test_closed_when_no_token_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MSB_OPERATOR_TOKEN", raising=False)
    monkeypatch.setattr(settings, "operator_token", "")
    r = TestClient(create_app()).get("/ops/background", headers=_auth("x"))
    assert r.status_code == 503


def test_returns_snapshot(client: TestClient) -> None:
    r = client.get("/ops/background", headers=_auth("tok"))
    assert r.status_code == 200
    assert r.json()["subsystems"]["cron"]["state"] == "ok"


def test_no_write_methods(client: TestClient) -> None:
    for method in ("post", "put", "patch", "delete"):
        r = getattr(client, method)("/ops/background", headers=_auth("tok"))
        assert r.status_code == 405, method
```

Check the 401-vs-503 behaviour against `tests/cron/test_cron_api.py` (it asserts 503 when the token is unset). If `require_operator` returns a different code for a missing header while a token *is* set, change the first assertion to the code `tests/cron/test_cron_api.py` expects for the same case. The route must behave exactly like `/cron/*`.

- [x] **Step 2: Run to verify it fails**

Run: `PY -m pytest -q tests/ops/test_background_api.py`
Expected: ImportError `cannot import name 'ops_background'`.

- [x] **Step 3: Implement the router**

Create `src/msb_v3/api/ops_background.py`:

```python
"""Ops background router: ``GET /ops/background``.

One read-only snapshot of what runs behind the runtime (cron, governance,
automation, wake, PLEI), for the desktop cockpit's Background section.
Operator-gated like /cron and /wake, because job ids, budgets and inbox
depth are operational detail. There are no write routes here, and there must never be.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends

from msb_v3.api.auth import require_operator
from msb_v3.ops.background import SnapshotCache, build_snapshot

router = APIRouter(tags=["ops"])

_cache = SnapshotCache()


@router.get("/background", dependencies=[Depends(require_operator)])
async def background() -> Dict[str, Any]:
    """Per-subsystem state (ok / warn / fail / unknown) with detail."""
    return await _cache.get(lambda: build_snapshot())
```

(The lambda makes the route look up `build_snapshot` when it's called, so the test's monkeypatch of `ops_background.build_snapshot` takes effect.)

- [x] **Step 4: Mount it**

In `src/msb_v3/api/app.py`, add the import in alphabetical position among the other `from msb_v3.api.… import router as …_router` lines:

```python
from msb_v3.api.ops_background import router as ops_background_router
```

and add after the `automation_router` mount line (`app.include_router(automation_router, prefix="/automation", tags=["automation"])`):

```python
    app.include_router(ops_background_router, prefix="/ops", tags=["ops"])
```

- [x] **Step 5: Classify it in the surface map**

In `docs/SURFACE.md`, in the router table (rows are alphabetical by path), add:

```markdown
| `api/ops_background.py` | OPTIONAL | `/ops/background` — watch-only snapshot of background subsystems for the desktop cockpit's Background section (docs/superpowers/specs/2026-09-22-background-windows-design.md). Operator-gated, no write routes. |
```

and extend the existing `msb_v3/ops` row's justification by appending: ` Also \`background.py\`, the snapshot behind \`/ops/background\`.`

- [x] **Step 6: Run the route tests and the two surface gates**

Run: `PY -m pytest -q tests/ops/test_background_api.py tests/api/test_no_dead_routers.py tests/docs/test_surface_map.py`
Expected: all pass.

- [x] **Step 7: Commit**

```bash
git add src/msb_v3/api/ops_background.py src/msb_v3/api/app.py docs/SURFACE.md tests/ops/test_background_api.py
git commit -m "feat(api): GET /ops/background, operator-gated snapshot route"
```

---

### Task 6: Desktop bridge, validation, IPC and preload for the 5 read methods

**Files:**
- Modify: `desktop/src/main/bridge.js` (add methods after `listTasks`)
- Modify: `desktop/src/main/validate.js` (add validators)
- Modify: `desktop/src/main/index.js` (`registerIpc`, after `channel('listTasks', ...)`)
- Modify: `desktop/src/preload/index.js` (add methods after `listTasks`)
- Test: `desktop/test/bridge.test.js`, `desktop/test/validate.test.js`, `desktop/test/security.test.js`

**Interfaces:**
- Produces, on `window.msb` (each resolves `{ ok, status?, data?, error? }` like the existing methods):
  - `background()` → `GET /ops/background` (operator)
  - `cronJobs()` → `GET /cron/jobs` (operator); `data.jobs[]` with `job_id, schedule, enabled, next_run`
  - `cronHistory(jobId, limit?)` → `GET /cron/jobs/{jobId}/history?limit=N` (operator, default 20); `data.runs[]` with `started_at, status, trigger, duration_ms, error`
  - `pleiCalibrate()` → `GET /plei/calibrate`
  - `auditStream(limit?)` → `GET /cockpit/audit?limit=N` (default 50); `data.receipts[]`, oldest first

- [x] **Step 1: Write the failing tests**

Append to `desktop/test/bridge.test.js`:

```javascript
test('background() GETs /ops/background with the operator token', async () => {
  const m = await fakeMsb({ 'GET /ops/background': () => ({ json: { subsystems: {} } }) });
  const b = new MsbBridge('127.0.0.1', m.port, { operatorToken: 'OP' });
  const r = await b.background();
  await m.close();
  assert.equal(r.ok, true);
  assert.equal(m.seen[0].headers.authorization, 'Bearer OP');
});

test('background() fails closed without an operator token - no request sent', async () => {
  const m = await fakeMsb({ 'GET *': () => ({ json: {} }) });
  const b = new MsbBridge('127.0.0.1', m.port, {});
  const r = await b.background();
  await m.close();
  assert.equal(r.ok, false);
  assert.equal(m.seen.length, 0);
});

test('cronJobs() and cronHistory() hit the operator-gated cron reads', async () => {
  const m = await fakeMsb({
    'GET /cron/jobs': () => ({ json: { jobs: [] } }),
    'GET /cron/jobs/wake-agent/history': () => ({ json: { runs: [] } }),
  });
  const b = new MsbBridge('127.0.0.1', m.port, { operatorToken: 'OP' });
  await b.cronJobs();
  await b.cronHistory('wake-agent');
  await m.close();
  assert.equal(m.seen[0].url, '/cron/jobs');
  assert.equal(m.seen[1].url, '/cron/jobs/wake-agent/history?limit=20');
  assert.equal(m.seen[1].headers.authorization, 'Bearer OP');
});

test('pleiCalibrate() and auditStream() are open reads', async () => {
  const m = await fakeMsb({
    'GET /plei/calibrate': () => ({ json: { total_pairs: 0 } }),
    'GET /cockpit/audit': () => ({ json: { receipts: [] } }),
  });
  const b = new MsbBridge('127.0.0.1', m.port, {});
  const a = await b.pleiCalibrate();
  const s = await b.auditStream(10);
  await m.close();
  assert.equal(a.ok, true);
  assert.equal(s.ok, true);
  assert.equal(m.seen[1].url, '/cockpit/audit?limit=10');
});
```

Append to `desktop/test/validate.test.js` (it already imports `validate`; if not, add `const { validate } = require('../src/main/validate');`):

```javascript
test('cronHistory requires a slug jobId and an optional limit 1-200', () => {
  assert.equal(validate('cronHistory', { jobId: 'wake-agent' }).ok, true);
  assert.deepEqual(validate('cronHistory', { jobId: 'wake-agent' }).value, { jobId: 'wake-agent', limit: 20 });
  assert.equal(validate('cronHistory', { jobId: '../etc' }).ok, false);
  assert.equal(validate('cronHistory', { jobId: 'a b' }).ok, false);
  assert.equal(validate('cronHistory', {}).ok, false);
  assert.equal(validate('cronHistory', { jobId: 'x', limit: 201 }).ok, false);
});

test('auditStream takes an optional limit 1-500', () => {
  assert.deepEqual(validate('auditStream', {}).value, { limit: 50 });
  assert.equal(validate('auditStream', { limit: 500 }).ok, true);
  assert.equal(validate('auditStream', { limit: 0 }).ok, false);
});

test('background, cronJobs and pleiCalibrate take no arguments', () => {
  for (const ch of ['background', 'cronJobs', 'pleiCalibrate']) {
    assert.deepEqual(validate(ch, { anything: 1 }), { ok: true, value: {} });
  }
});
```

In `desktop/test/security.test.js`, replace the `expected` array in `'preload exposes exactly the allow-listed method names'` with:

```javascript
  const expected = [
    'attach', 'health', 'identity', 'cockpit', 'governanceStatus',
    'approvals', 'approve', 'killswitch', 'killswitchSet', 'memory', 'search',
    'listTasks', 'subscribeTask', 'unsubscribeTask', 'onTaskEvent', 'sendChat',
    'background', 'cronJobs', 'cronHistory', 'pleiCalibrate', 'auditStream',
  ].sort();
```

- [x] **Step 2: Run to verify they fail**

Run: `cd desktop && npm test`
Expected: failures such as `b.background is not a function`, the validate tests, and the preload list mismatch.

- [x] **Step 3: Implement the bridge methods**

In `desktop/src/main/bridge.js`, add after `listTasks(limit) {...}`:

```javascript
  // --- Background section (watch-only reads) -------------------------

  /** GET /ops/background - per-subsystem state snapshot. Operator-gated. */
  background() {
    return this._get('/ops/background', { operator: true });
  }

  /** GET /cron/jobs - scheduled jobs with next_run. Operator-gated. */
  cronJobs() {
    return this._get('/cron/jobs', { operator: true });
  }

  /**
   * GET /cron/jobs/{id}/history - newest runs first. Operator-gated.
   * @param {string} jobId
   * @param {number} [limit]
   */
  cronHistory(jobId, limit) {
    const n = limit || 20;
    return this._get(`/cron/jobs/${encodeURIComponent(jobId)}/history?limit=${encodeURIComponent(n)}`, {
      operator: true,
    });
  }

  /** GET /plei/calibrate - calibration report (reads the store only). */
  pleiCalibrate() {
    return this._get('/plei/calibrate');
  }

  /**
   * GET /cockpit/audit - last N governed-run receipts, oldest first.
   * @param {number} [limit]
   */
  auditStream(limit) {
    const n = limit || 50;
    return this._get(`/cockpit/audit?limit=${encodeURIComponent(n)}`);
  }
```

- [x] **Step 4: Implement the validators**

In `desktop/src/main/validate.js`, add below `const SESSION_ALLOWED = ...`:

```javascript
const JOB_ID_ALLOWED = /^[a-z0-9][a-z0-9_-]*$/;
```

and add inside `validators` (after `listTasks`):

```javascript
  cronHistory(payload) {
    const p = payload && typeof payload === 'object' ? payload : {};
    const jobId = cleanString(p.jobId, MAX_ID_LEN);
    if (!jobId || !JOB_ID_ALLOWED.test(jobId)) return fail('jobId must be a slug');
    const limit = cleanLimit(p.limit, 20, 200);
    if (limit === null) return fail('limit out of range');
    return ok({ jobId, limit });
  },

  auditStream(payload) {
    const p = payload && typeof payload === 'object' ? payload : {};
    const limit = cleanLimit(p.limit, 50, MAX_LIMIT);
    if (limit === null) return fail('limit out of range');
    return ok({ limit });
  },
```

(`MAX_LIMIT` is already 500. `background`, `cronJobs` and `pleiCalibrate` need no validator: `validate()` passes channels without one with `{}`.)

- [x] **Step 5: Register the channels**

In `desktop/src/main/index.js` `registerIpc()`, after `channel('listTasks', (a) => bridge.listTasks(a.limit));` add:

```javascript
  channel('background', () => bridge.background());
  channel('cronJobs', () => bridge.cronJobs());
  channel('cronHistory', (a) => bridge.cronHistory(a.jobId, a.limit));
  channel('pleiCalibrate', () => bridge.pleiCalibrate());
  channel('auditStream', (a) => bridge.auditStream(a.limit));
```

- [x] **Step 6: Expose them in the preload**

In `desktop/src/preload/index.js`, add after the `listTasks` entry:

```javascript
  /** GET /ops/background - watch-only snapshot of background subsystems. */
  background: () => ipcRenderer.invoke('msb:background'),

  /** GET /cron/jobs - scheduled jobs. */
  cronJobs: () => ipcRenderer.invoke('msb:cronJobs'),

  /**
   * GET /cron/jobs/{id}/history - recent runs of one job.
   * @param {string} jobId
   * @param {number} [limit]
   */
  cronHistory: (jobId, limit) =>
    ipcRenderer.invoke('msb:cronHistory', { jobId: str(jobId), limit: int(limit) }),

  /** GET /plei/calibrate - calibration report. */
  pleiCalibrate: () => ipcRenderer.invoke('msb:pleiCalibrate'),

  /**
   * GET /cockpit/audit - recent governed-run receipts.
   * @param {number} [limit]
   */
  auditStream: (limit) => ipcRenderer.invoke('msb:auditStream', { limit: int(limit) }),
```

- [x] **Step 7: Run to verify they pass**

Run: `cd desktop && npm test`
Expected: all tests pass (including the existing security tests).

- [x] **Step 8: Commit**

```bash
git add desktop/src/main/bridge.js desktop/src/main/validate.js desktop/src/main/index.js desktop/src/preload/index.js desktop/test/bridge.test.js desktop/test/validate.test.js desktop/test/security.test.js
git commit -m "feat(desktop): read-only bridge methods for the Background section"
```

---

### Task 7: Visible-only poller

**Files:**
- Create: `desktop/src/renderer/poller.js`
- Test: `desktop/test/poller.test.js`

**Interfaces:**
- Produces: `createPoller({ load, intervalMs, backoffMs = 30000, isVisible, onResult = () => {}, setTimer = setTimeout, clearTimer = clearTimeout })` returning `{ start(), stop(), wake(), running }`. In the browser it is `window.MsbPoller.createPoller`; in Node it is `require('../src/renderer/poller').createPoller`.
  - `load()` resolves a `{ ok }` result; `onResult(result)` is called after every load (a thrown error becomes `{ ok: false, error }`).
  - The next load is scheduled `intervalMs` after a successful load and `backoffMs` after a failed one.
  - While `isVisible()` is false it does not load or reschedule; `wake()` (call it on `visibilitychange`) resumes it immediately.

- [x] **Step 1: Write the failing tests**

Create `desktop/test/poller.test.js`:

```javascript
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { createPoller } = require('../src/renderer/poller');

function fakeTimers() {
  const pending = [];
  return {
    pending,
    setTimer: (fn, ms) => {
      const t = { fn, ms };
      pending.push(t);
      return t;
    },
    clearTimer: (t) => {
      const i = pending.indexOf(t);
      if (i >= 0) pending.splice(i, 1);
    },
    async fire() {
      const t = pending.shift();
      await t.fn();
    },
  };
}

const flush = () => new Promise((r) => setImmediate(r));

function setup({ visible = true, result = { ok: true } } = {}) {
  const timers = fakeTimers();
  const calls = [];
  const view = { visible };
  const results = [];
  const poller = createPoller({
    load: async () => {
      calls.push(1);
      return typeof result === 'function' ? result() : result;
    },
    intervalMs: 5000,
    backoffMs: 30000,
    isVisible: () => view.visible,
    onResult: (r) => results.push(r),
    setTimer: timers.setTimer,
    clearTimer: timers.clearTimer,
  });
  return { poller, timers, calls, view, results };
}

test('loads immediately, then every intervalMs while visible', async () => {
  const { poller, timers, calls } = setup();
  poller.start();
  await flush();
  assert.equal(calls.length, 1);
  assert.equal(timers.pending.length, 1);
  assert.equal(timers.pending[0].ms, 5000);
  await timers.fire();
  assert.equal(calls.length, 2);
});

test('a failed load backs off to backoffMs', async () => {
  const { poller, timers, results } = setup({ result: { ok: false, error: 'MSB_UNAVAILABLE' } });
  poller.start();
  await flush();
  assert.equal(timers.pending[0].ms, 30000);
  assert.equal(results[0].ok, false);
});

test('a thrown load is reported as a failed result and backs off', async () => {
  const { poller, timers, results } = setup({
    result: () => {
      throw new Error('boom');
    },
  });
  poller.start();
  await flush();
  assert.equal(results[0].ok, false);
  assert.match(results[0].error, /boom/);
  assert.equal(timers.pending[0].ms, 30000);
});

test('does not load while hidden; wake() resumes when visible', async () => {
  const { poller, timers, calls, view } = setup({ visible: false });
  poller.start();
  await flush();
  assert.equal(calls.length, 0);
  assert.equal(timers.pending.length, 0);
  view.visible = true;
  poller.wake();
  await flush();
  assert.equal(calls.length, 1);
});

test('going hidden stops rescheduling', async () => {
  const { poller, timers, calls, view } = setup();
  poller.start();
  await flush();
  view.visible = false;
  await timers.fire();
  assert.equal(calls.length, 1);
  assert.equal(timers.pending.length, 0);
});

test('stop() clears the timer and ends polling', async () => {
  const { poller, timers, calls } = setup();
  poller.start();
  await flush();
  poller.stop();
  assert.equal(timers.pending.length, 0);
  assert.equal(poller.running, false);
  poller.wake();
  await flush();
  assert.equal(calls.length, 1);
});

test('start() twice does not double-poll', async () => {
  const { poller, calls } = setup();
  poller.start();
  poller.start();
  await flush();
  assert.equal(calls.length, 1);
});
```

- [x] **Step 2: Run to verify it fails**

Run: `cd desktop && node --test test/poller.test.js`
Expected: `Cannot find module '../src/renderer/poller'`.

- [x] **Step 3: Implement**

Create `desktop/src/renderer/poller.js`:

```javascript
/**
 * MSB v3 Desktop - visible-only poller.
 *
 * Calls `load()` now and then every `intervalMs` while running and the
 * document is visible; after a failed load it waits `backoffMs` instead.
 * While hidden it neither loads nor reschedules; `wake()` (wire it to
 * `visibilitychange`) resumes it. Timers are injectable so Node tests can
 * drive it without a DOM.
 *
 * Browser: window.MsbPoller.createPoller. Node: module.exports.createPoller.
 */

(function (root) {
  'use strict';

  function createPoller({
    load,
    intervalMs,
    backoffMs = 30000,
    isVisible,
    onResult = () => {},
    setTimer = setTimeout,
    clearTimer = clearTimeout,
  }) {
    let timer = null;
    let running = false;
    let inFlight = false;

    async function tick() {
      timer = null;
      if (!running || !isVisible()) return; // paused until wake()
      inFlight = true;
      let result;
      try {
        result = await load();
      } catch (err) {
        result = { ok: false, error: String((err && err.message) || err) };
      }
      inFlight = false;
      onResult(result);
      if (running) timer = setTimer(tick, result && result.ok ? intervalMs : backoffMs);
    }

    return {
      start() {
        if (running) return;
        running = true;
        tick();
      },
      stop() {
        running = false;
        if (timer) clearTimer(timer);
        timer = null;
      },
      wake() {
        if (running && !timer && !inFlight && isVisible()) tick();
      },
      get running() {
        return running;
      },
    };
  }

  if (typeof module !== 'undefined' && module.exports) module.exports = { createPoller };
  else root.MsbPoller = Object.freeze({ createPoller });
})(this);
```

- [x] **Step 4: Run to verify it passes**

Run: `cd desktop && node --test test/poller.test.js`
Expected: 7 passed.

- [x] **Step 5: Commit**

```bash
git add desktop/src/renderer/poller.js desktop/test/poller.test.js
git commit -m "feat(desktop): visible-only poller with failure back-off"
```

---

### Task 8: Background section views, wired into the cockpit

**Files:**
- Create: `desktop/src/renderer/background.js`
- Modify: `desktop/src/renderer/index.html` (script tags + CSS)
- Modify: `desktop/src/renderer/app.js` (`render()`, `renderTabsBar()`, `renderTabBody()`)
- Test: `desktop/test/security.test.js` (extend renderer checks to the new files)

**Interfaces:**
- Consumes: the `window.msb` methods from Task 6 plus the existing `governanceStatus()`/`approvals()`; `window.MsbPoller.createPoller` (Task 7); `el()` and `clear()` from `app.js` (global function declarations, resolved at call time).
- Produces: `window.MsbBackground = { mount(): HTMLElement, unmount(): void }`. `mount()` is idempotent and starts polling the current view; `unmount()` stops all polling.

- [x] **Step 1: Write the failing security assertions**

In `desktop/test/security.test.js`, add below `const rendererApp = read('renderer/app.js');`:

```javascript
const rendererBackground = read('renderer/background.js');
const rendererPoller = read('renderer/poller.js');
```

and add these tests at the end:

```javascript
test('background + poller: no network, no interpolated innerHTML, no inline handlers', () => {
  for (const [name, src] of [['background.js', rendererBackground], ['poller.js', rendererPoller]]) {
    assert.doesNotMatch(src, /\bfetch\(|XMLHttpRequest|WebSocket|EventSource|import\(/, name);
    assert.doesNotMatch(src, /innerHTML/, name);
    assert.doesNotMatch(src, /setAttribute\(\s*['"]on/, name);
  }
});

test('background section is watch-only: it calls no write method', () => {
  assert.doesNotMatch(rendererBackground, /msb\.(approve|killswitchSet|sendChat)\(/);
});

test('index.html loads poller.js and background.js before app.js', () => {
  const order = [...rendererHtml.matchAll(/<script src="([^"]+)"/g)].map((m) => m[1]);
  assert.deepEqual(order, ['poller.js', 'background.js', 'app.js']);
});
```

- [x] **Step 2: Run to verify it fails**

Run: `cd desktop && npm test`
Expected: ENOENT for `renderer/background.js`.

- [x] **Step 3: Implement `background.js`**

Create `desktop/src/renderer/background.js`:

```javascript
/**
 * MSB v3 Desktop - Background section (watch-only).
 *
 * Views of what runs behind the runtime; design:
 * docs/superpowers/specs/2026-09-22-background-windows-design.md. Phase 1
 * ships six views; Machine services (launchd) arrives in phase 2. Only the
 * visible view polls, and polling pauses while the window is hidden.
 * Every call is a read: there are no control actions here.
 *
 * Loaded after poller.js and before app.js; uses app.js's el()/clear() at
 * call time only.
 */

(function () {
  'use strict';

  const STATES = ['ok', 'warn', 'fail', 'unknown'];
  const SUBSYSTEM_VIEW = { cron: 'cron', governance: 'governance', automation: 'automation', wake: 'automation', plei: 'plei' };

  const bg = {
    view: 'overview',
    snapshot: null,
    audit: [],
    cronJobs: [],
    cronHistory: {},
    openJob: null,
    governance: null,
    approvals: [],
    calibrate: null,
    lastGoodAt: null,
    lastError: '',
  };
  let root = null;
  let poller = null;

  // --- loads (each returns the primary { ok } result for back-off) ------

  async function loadOverview() {
    const r = await window.msb.background();
    if (r.ok) bg.snapshot = r.data;
    return r;
  }

  async function loadActivity() {
    const r = await window.msb.auditStream(50);
    if (r.ok) bg.audit = (r.data && r.data.receipts) || [];
    return r;
  }

  async function loadCron() {
    const [jobs, snap] = await Promise.all([window.msb.cronJobs(), window.msb.background()]);
    if (jobs.ok) bg.cronJobs = (jobs.data && jobs.data.jobs) || [];
    if (snap.ok) bg.snapshot = snap.data;
    if (jobs.ok && bg.openJob) await loadHistory(bg.openJob);
    return jobs;
  }

  async function loadHistory(jobId) {
    const h = await window.msb.cronHistory(jobId);
    if (h.ok) bg.cronHistory[jobId] = (h.data && h.data.runs) || [];
  }

  async function loadGovernance() {
    const [gov, ap] = await Promise.all([window.msb.governanceStatus(), window.msb.approvals()]);
    if (gov.ok) bg.governance = gov.data;
    if (ap.ok) bg.approvals = (ap.data && ap.data.items) || [];
    return gov;
  }

  async function loadPlei() {
    const [snap, cal] = await Promise.all([window.msb.background(), window.msb.pleiCalibrate()]);
    if (snap.ok) bg.snapshot = snap.data;
    if (cal.ok) bg.calibrate = cal.data;
    return snap.ok ? cal : snap;
  }

  const VIEWS = [
    { id: 'overview', label: 'Overview', intervalMs: 5000, load: loadOverview, render: renderOverview },
    { id: 'activity', label: 'Activity', intervalMs: 10000, load: loadActivity, render: renderActivity },
    { id: 'cron', label: 'Scheduled jobs', intervalMs: 10000, load: loadCron, render: renderCron },
    { id: 'governance', label: 'Governance', intervalMs: 10000, load: loadGovernance, render: renderGovernance },
    { id: 'automation', label: 'Automation & wake', intervalMs: 10000, load: loadOverview, render: renderAutomation },
    { id: 'plei', label: 'PLEI', intervalMs: 10000, load: loadPlei, render: renderPlei },
  ];

  // --- lifecycle -----------------------------------------------------

  function onResult(r) {
    if (r && r.ok) {
      bg.lastGoodAt = Date.now();
      bg.lastError = '';
    } else {
      bg.lastError = (r && (r.error || r.detail)) || 'read failed';
    }
    draw();
  }

  function startView(id) {
    if (poller) poller.stop();
    const v = VIEWS.find((x) => x.id === id) || VIEWS[0];
    bg.view = v.id;
    poller = window.MsbPoller.createPoller({
      load: v.load,
      intervalMs: v.intervalMs,
      isVisible: () => document.visibilityState === 'visible',
      onResult,
    });
    draw();
    poller.start();
  }

  document.addEventListener('visibilitychange', () => {
    if (poller) poller.wake();
  });

  function mount() {
    if (!root) root = el('div', { class: 'bg' });
    if (!poller) startView(bg.view);
    else draw();
    return root;
  }

  function unmount() {
    if (poller) poller.stop();
    poller = null;
  }

  // --- drawing ---------------------------------------------------------

  function draw() {
    if (!root) return;
    clear(root);
    const side = el('div', { class: 'bg-side' });
    for (const v of VIEWS) {
      side.appendChild(
        el('button', { class: `btn bg-nav${v.id === bg.view ? ' active' : ''}`, text: v.label, onclick: () => startView(v.id) })
      );
    }
    const main = el('div', { class: 'bg-main' });
    main.appendChild(freshness());
    main.appendChild((VIEWS.find((x) => x.id === bg.view) || VIEWS[0]).render());
    root.appendChild(side);
    root.appendChild(main);
  }

  function ago(t) {
    return `${Math.round((Date.now() - t) / 1000)}s ago`;
  }

  function freshness() {
    if (!bg.lastError) {
      return el('div', { class: 'detail', text: bg.lastGoodAt ? `updated ${ago(bg.lastGoodAt)}` : 'loading...' });
    }
    const last = bg.lastGoodAt ? `last good data: ${ago(bg.lastGoodAt)}` : 'no data yet';
    return el('div', { class: 'err', text: `read failed (${bg.lastError}) - ${last}; retrying every 30s` });
  }

  function stateBadge(s) {
    const k = STATES.includes(s) ? s : 'unknown';
    return el('span', { class: `badge st-${k}`, text: k.toUpperCase() });
  }

  function subEntry(name) {
    const subs = (bg.snapshot && bg.snapshot.subsystems) || {};
    return subs[name] || { state: 'unknown', detail: {}, error: 'no snapshot yet' };
  }

  function summarize(name, d) {
    switch (name) {
      case 'cron':
        return `${d.job_count ?? '?'} jobs; failed ${(d.failed || []).length}; overdue ${(d.overdue || []).length}${
          d.scheduler_enabled === false ? '; scheduler OFF' : ''
        }`;
      case 'governance':
        return `kill switch ${d.killswitch_armed ? 'ARMED' : 'off'}; ${d.pending_approvals ?? 0} pending approvals`;
      case 'automation':
        return `${d.dry_run ? 'dry-run' : 'LIVE'}; spent $${d.spent_usd ?? '?'} of $${d.cap_usd ?? '?'}`;
      case 'wake':
        return `${d.enabled ? '' : 'disabled; '}${d.pending ?? '?'} pending; ${d.outbox_count ?? '?'} replies`;
      case 'plei':
        return `${d.predictions ?? '?'} predictions; ${d.pairs ?? '?'} pairs; chain ${d.chain_ok ? 'ok' : 'BROKEN'}`;
      default:
        return '';
    }
  }

  function card(title) {
    const c = el('div', { class: 'card' });
    c.appendChild(el('h2', { text: title }));
    return c;
  }

  function renderOverview() {
    const c = card('Background overview');
    const subs = (bg.snapshot && bg.snapshot.subsystems) || {};
    const names = Object.keys(subs);
    if (!names.length) {
      c.appendChild(el('div', { class: 'empty', text: 'No snapshot yet.' }));
      return c;
    }
    const grid = el('div', { class: 'grid' });
    for (const name of names) {
      const e = subs[name] || {};
      const tile = el('div', { class: 'card bg-tile', onclick: () => startView(SUBSYSTEM_VIEW[name] || 'overview') });
      tile.appendChild(el('h2', { text: name }));
      tile.appendChild(stateBadge(e.state));
      tile.appendChild(el('div', { class: 'detail', text: e.error || summarize(name, e.detail || {}) }));
      grid.appendChild(tile);
    }
    c.appendChild(grid);
    return c;
  }

  function renderActivity() {
    const c = card('Activity - governed runs');
    c.appendChild(
      el('div', {
        class: 'detail',
        text: 'Governed-run receipts, newest first. Cron runs are under Scheduled jobs; the full audit-chain feed arrives in phase 3.',
      })
    );
    if (!bg.audit.length) {
      c.appendChild(el('div', { class: 'empty', text: 'No receipts yet.' }));
      return c;
    }
    const list = el('ul', { class: 'list' });
    for (const rec of bg.audit.slice().reverse()) {
      const verdict = (rec.execution_result && rec.execution_result.verdict) || rec.moie_verdict || '?';
      const li = el('li', {});
      li.appendChild(el('span', { class: 'badge kind', text: String(verdict) }));
      li.appendChild(el('strong', { text: ` ${String(rec.intent || '(no intent)').slice(0, 120)} ` }));
      li.appendChild(el('span', { class: 'detail', text: String(rec.ts || rec.timestamp || rec.created_at || '') }));
      list.appendChild(li);
    }
    c.appendChild(list);
    return c;
  }

  async function toggleJob(jobId) {
    bg.openJob = bg.openJob === jobId ? null : jobId;
    if (bg.openJob) await loadHistory(jobId);
    draw();
  }

  function renderCron() {
    const c = card('Scheduled jobs (cron)');
    const d = subEntry('cron').detail || {};
    const failed = new Set(d.failed || []);
    const overdue = new Set(d.overdue || []);
    if (d.scheduler_enabled === false) {
      c.appendChild(el('div', { class: 'err', text: 'Scheduler is OFF (MSB_CRON_ENABLED=0): no job will fire.' }));
    }
    if (!bg.cronJobs.length) {
      c.appendChild(el('div', { class: 'empty', text: 'No jobs.' }));
      return c;
    }
    const list = el('ul', { class: 'list' });
    for (const j of bg.cronJobs) {
      const li = el('li', {});
      if (!j.enabled) li.appendChild(el('span', { class: 'badge kind', text: 'OFF' }));
      else li.appendChild(stateBadge(failed.has(j.job_id) ? 'fail' : overdue.has(j.job_id) ? 'warn' : 'ok'));
      li.appendChild(el('strong', { text: ` ${j.job_id} ` }));
      li.appendChild(el('span', { class: 'detail', text: `${j.schedule} | next ${j.next_run || '?'}` }));
      const open = bg.openJob === j.job_id;
      li.appendChild(
        el('button', { class: 'btn', style: 'margin-left:8px', text: open ? 'Hide history' : 'History', onclick: () => toggleJob(j.job_id) })
      );
      if (open) {
        const runs = bg.cronHistory[j.job_id] || [];
        const hl = el('ul', { class: 'list' });
        for (const r of runs) {
          const ms = r.duration_ms == null ? '' : `${Math.round(r.duration_ms)}ms`;
          hl.appendChild(el('li', { class: 'mono', text: `${r.started_at}  ${r.status}  ${r.trigger}  ${ms}  ${r.error || ''}` }));
        }
        if (!runs.length) hl.appendChild(el('li', { class: 'empty', text: 'No runs recorded.' }));
        li.appendChild(hl);
      }
      list.appendChild(li);
    }
    c.appendChild(list);
    return c;
  }

  function renderGovernance() {
    const c = card('Governance (read-only)');
    const g = bg.governance;
    if (!g) {
      c.appendChild(el('div', { class: 'empty', text: 'No governance data yet.' }));
      return c;
    }
    const ks = g.killswitch || {};
    c.appendChild(el('div', {}, stateBadge(ks.armed ? 'fail' : 'ok'), ` Kill switch ${ks.armed ? 'ARMED' : 'off'}${ks.reason ? ` - ${ks.reason}` : ''}`));
    const scopes = (ks.scopes || []).filter((s) => !s.error);
    if (scopes.length) {
      c.appendChild(el('div', { class: 'detail', text: `armed scopes: ${scopes.map((s) => `${s.scope_type}:${s.scope_id}`).join(', ')}` }));
    }
    c.appendChild(el('h2', { text: 'Budgets', style: 'margin-top:12px' }));
    const bl = el('ul', { class: 'list' });
    for (const [cat, b] of Object.entries(g.budgets || {})) {
      const pct = b.limit > 0 ? Math.round((100 * b.spent) / b.limit) : null;
      bl.appendChild(
        el('li', {}, stateBadge(pct !== null && pct > 80 ? 'warn' : 'ok'), ` ${cat}: ${b.spent} / ${b.limit > 0 ? b.limit : 'unlimited'}${pct === null ? '' : ` (${pct}%)`}`)
      );
    }
    c.appendChild(bl);
    c.appendChild(el('h2', { text: `Pending approvals (${bg.approvals.length})`, style: 'margin-top:12px' }));
    const al = el('ul', { class: 'list' });
    for (const a of bg.approvals) al.appendChild(el('li', { text: `${a.kind || '?'}  ${a.id}  ${a.summary || a.reason || ''}` }));
    if (!bg.approvals.length) al.appendChild(el('li', { class: 'empty', text: 'None.' }));
    c.appendChild(al);
    c.appendChild(el('div', { class: 'detail', text: 'Approve or reject from the main approvals panel; this view is watch-only.' }));
    return c;
  }

  function renderAutomation() {
    const c = card('Automation & wake');
    for (const name of ['automation', 'wake']) {
      const e = subEntry(name);
      c.appendChild(el('div', { style: 'margin:8px 0' }, stateBadge(e.state), ` ${name}: ${e.error || summarize(name, e.detail || {})}`));
      c.appendChild(el('div', { class: 'mono', text: JSON.stringify(e.detail || {}, null, 2) }));
    }
    return c;
  }

  function renderPlei() {
    const c = card('PLEI calibration');
    const e = subEntry('plei');
    const d = e.detail || {};
    c.appendChild(el('div', {}, stateBadge(e.state), ` ${e.error || summarize('plei', d)}`));
    if (d.last_forecast_at) c.appendChild(el('div', { class: 'detail', text: `last prediction ${d.last_forecast_at}` }));
    if (d.chain_ok === false) c.appendChild(el('div', { class: 'err', text: `chain: ${d.chain_message}` }));
    c.appendChild(el('h2', { text: 'Calibration report', style: 'margin-top:12px' }));
    c.appendChild(el('div', { class: 'mono', text: bg.calibrate ? JSON.stringify(bg.calibrate, null, 2).slice(0, 4000) : 'loading...' }));
    return c;
  }

  window.MsbBackground = Object.freeze({ mount, unmount });
})();
```

- [x] **Step 4: Load the scripts and add the CSS**

In `desktop/src/renderer/index.html`, replace `<script src="app.js"></script>` with:

```html
  <script src="poller.js"></script>
  <script src="background.js"></script>
  <script src="app.js"></script>
```

and add inside `<style>`, before `</style>`:

```css
    .bg { display: flex; gap: 16px; align-items: flex-start; }
    .bg-side { display: flex; flex-direction: column; gap: 6px; min-width: 170px; }
    .bg-nav { background: #1a1a1a; text-align: left; }
    .bg-nav.active { background: #2563eb; }
    .bg-main { flex: 1; display: flex; flex-direction: column; gap: 8px; min-width: 0; }
    .bg-tile { cursor: pointer; }
    .bg .mono { white-space: pre-wrap; margin-left: 0; }
    .badge.st-ok { background: #0a3d0a; color: #4ade80; }
    .badge.st-warn { background: #3d3208; color: #fbbf24; }
    .badge.st-fail { background: #3d0a0a; color: #f87171; }
    .badge.st-unknown { background: #2a2a2a; color: #888; }
```

- [x] **Step 5: Wire the tab into `app.js`**

In `renderTabsBar()`, add a fourth entry to the tab list:

```javascript
    { id: 'background', label: 'Background' },
```

In `renderTabBody()`, after `taskEventsListEl = null; ...`, add:

```javascript
  if (state.activeTab !== 'background') window.MsbBackground.unmount();
```

and after the `tasks` line add:

```javascript
  if (state.activeTab === 'background') c.tabBody.appendChild(window.MsbBackground.mount());
```

In `render()`, inside the `NOT_ATTACHED || OFFLINE || BLOCKED` branch, before `return;`, add:

```javascript
    window.MsbBackground.unmount(); // nothing to watch without a runtime
```

- [x] **Step 6: Run the desktop tests**

Run: `cd desktop && npm test`
Expected: all pass. `npm run typecheck` should still pass too (the files are plain JS; if `tsconfig.json` includes `src/renderer/**`, fix any type complaint it reports in the new files only).

- [x] **Step 7: Commit**

```bash
git add desktop/src/renderer/background.js desktop/src/renderer/index.html desktop/src/renderer/app.js desktop/test/security.test.js
git commit -m "feat(desktop): Background section - six watch-only views"
```

---

### Task 9: Full verification against the real runtime

**Files:** none changed unless a check fails.

- [x] **Step 1: Run the full non-live Python suite**

Run: `PY -m pytest -q -m "not live" -n auto`
Expected: the new tests pass. Compare any failures against the known flaky set (`tests/plei/test_plei_is_msb_v3.py` under xdist load, `tests/docs/test_doc_records.py`, `tests/db/test_schema_stamping.py::test_live_data_dir_is_fully_stamped`, which fail without these changes too). Any *other* failure is yours to fix. Also run `git status --short` afterwards: only `.plei/calibration.jsonl` may show as modified (the known leak fixed on the unmerged `fix/plei-calibration-test-isolation` branch); restore it with `git checkout -- .plei/calibration.jsonl`.

- [x] **Step 2: Run the desktop suite**

Run: `cd desktop && npm test && npm run typecheck`
Expected: pass.

- [x] **Step 3: Check the route against a scratch server started from the worktree**

The live runtime on `:8766` runs the main checkout's code, so it doesn't have the new route yet. Start a throwaway server from the worktree on another port. It shares the live data directories, and the route only reads:

```bash
PYTHONPATH=$PWD/src MSB_CRON_ENABLED=0 /opt/homebrew/Caskroom/miniforge/base/bin/python -m uvicorn msb_v3.api.app:create_app --factory --port 8799
```

(Run it in the background. If the app needs other env from `.env`, start it the way `start.sh` does, with the port changed.) Then:

```bash
curl -s -H "Authorization: Bearer $MSB_OPERATOR_TOKEN" http://127.0.0.1:8799/ops/background | python3 -m json.tool
```

Expected: five subsystems, each with a `state` in ok/warn/fail/unknown, and real detail (cron `job_count` > 0, a `wake` entry, `plei` predictions count). Stop the scratch server afterwards. `MSB_CRON_ENABLED=0` stops the scratch server's scheduler from firing jobs.

- [ ] **Step 4: Look at it in the cockpit**

Point the desktop app at the scratch server (attach accepts a port; if the UI has no port field, start it with the env var the main process reads for the port. Check `MSB_PORT` in `desktop/src/main/index.js`) and open the Background tab. Confirm:
- Overview shows five coloured tiles with summaries, and clicking a tile opens its view.
- Scheduled jobs lists the real jobs; "History" shows runs.
- Governance shows the kill switch and budgets and has **no** approve/reject buttons.
- Minimising the window stops requests: watch the scratch server's access log go quiet, then resume on restore.
- Stopping the scratch server shows "read failed ... last good data: Ns ago" and the retry slows to 30 s.

Take one screenshot of the Overview for the handoff.

- [x] **Step 5: Final commit (only if Steps 1–4 required fixes)**

```bash
git add -A && git commit -m "fix(background): issues found in end-to-end check"
```

(Only stage files from this plan; check `git status` first.)
