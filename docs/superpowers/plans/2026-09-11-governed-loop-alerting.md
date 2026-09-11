# Governed-Loop Alerting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a new in-process cron action, `alert_check`, that watches killswitch state, ActionGate BLOCK/FAIL rate, and system health, and pushes an edge-triggered Telegram notification (via the existing Hermes agent's `hermes send` CLI) when something crosses a threshold — and again when it recovers.

**Architecture:** One new action function in the existing cron-action registry (`src/msb_v3/cron/actions.py`), reading killswitch and ActionGate state via direct in-process imports (matching `action_health_check` and `flywheel/health_bridge.py`'s existing conventions — no HTTP self-calls), calling the already-existing `system_health()` function directly, and shelling out to `hermes send` as a bounded subprocess. Seeded as a cron job on server startup, exactly like the existing `wake-agent` job.

**Tech Stack:** Python 3.12, existing `msb_v3.cron` scheduler, `prometheus_client` (already a dependency), stdlib `subprocess` + `json`.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-09-11-governed-loop-alerting-design.md` — read it before starting; this plan implements it exactly, with two decisions the spec explicitly deferred to this plan (both resolved below): killswitch/metrics reads are **in-process imports**, not HTTP (matches `action_health_check`'s existing pattern); `ensure_alert_check_job()` lives in `src/msb_v3/cron/actions.py` (co-located with the action itself, not `wake/runner.py`).
- Every new settings field follows the existing `field(default_factory=lambda: os.getenv("MSB_...", "..."))` pattern in `src/msb_v3/core/config.py` — copy the exact style already used for `wake_schedule` etc.
- Action functions must never raise — every failure path returns `_fail(...)`. The scheduler's `run_action()` catches exceptions as a last resort, but this action catches per-rule so one rule's failure doesn't stop the others from being evaluated.
- No new processes, no new launchd units.

---

### Task 1: Alert state persistence helpers

**Files:**
- Modify: `src/msb_v3/core/config.py` (add one field, near the `cron_http_hosts` field in the "Cron scheduler" block)
- Modify: `src/msb_v3/cron/actions.py` (add helpers, before the `# --- registry ---` section)
- Test: `tests/cron/test_actions.py` (append)

**Interfaces:**
- Produces: `_alert_state_path() -> Path`, `_load_alert_state() -> Dict[str, Any]`, `_save_alert_state(state: Dict[str, Any]) -> None`. Task 2 calls these directly.
- Default state shape (returned by `_load_alert_state()` when no file exists or it's corrupt):
  ```python
  {
      "actiongate_baseline": {"failed": 0.0, "denied": 0.0},
      "actiongate_window_start": None,
      "consecutive_degraded": 0,
      "alerts_active": {"killswitch": False, "actiongate_rate": False, "system_degraded": False},
  }
  ```

- [ ] **Step 1: Add the `alert_state_path` setting**

In `src/msb_v3/core/config.py`, find this existing field (in the "Cron scheduler (the heartbeat)" block):

```python
    cron_http_hosts: str = field(default_factory=lambda: os.getenv("MSB_CRON_HTTP_HOSTS", "127.0.0.1,localhost,::1"))
```

Add immediately after it:

```python
    # Where alert_check's edge-trigger state (last-known killswitch/ActionGate/
    # health state) persists between polls. Empty = derive from db_path
    # (beside cron.db under data/cron/).
    alert_state_path: str = field(default_factory=lambda: os.getenv("MSB_ALERT_STATE_PATH", ""))
```

- [ ] **Step 2: Write the failing tests**

In `tests/cron/test_actions.py`, append:

```python
def test_alert_state_roundtrip(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(actions.settings, "db_path", str(tmp_path / "msb.db"))
    monkeypatch.setattr(actions.settings, "alert_state_path", "")
    state = actions._load_alert_state()
    assert state["alerts_active"] == {
        "killswitch": False,
        "actiongate_rate": False,
        "system_degraded": False,
    }
    state["consecutive_degraded"] = 3
    actions._save_alert_state(state)
    reloaded = actions._load_alert_state()
    assert reloaded["consecutive_degraded"] == 3


def test_alert_state_defaults_on_corrupt_file(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(actions.settings, "db_path", str(tmp_path / "msb.db"))
    monkeypatch.setattr(actions.settings, "alert_state_path", "")
    path = actions._alert_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json{{{")
    state = actions._load_alert_state()
    assert state["consecutive_degraded"] == 0
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd ~/projects/AI-Agents/msb-v3 && /opt/homebrew/Caskroom/miniforge/base/bin/python -m pytest tests/cron/test_actions.py -k alert_state -v`
Expected: FAIL with `AttributeError: module 'msb_v3.cron.actions' has no attribute '_load_alert_state'` (or similar — the helpers don't exist yet).

- [ ] **Step 4: Implement the helpers**

In `src/msb_v3/cron/actions.py`, add `import subprocess` to the top imports (alongside the existing `import sqlite3`), then add this block right before the `# --- registry ---` comment near the bottom:

```python
# --- alert_check -----------------------------------------------------------

_ALERT_STATE_DEFAULT: Dict[str, Any] = {
    "actiongate_baseline": {"failed": 0.0, "denied": 0.0},
    "actiongate_window_start": None,
    "consecutive_degraded": 0,
    "alerts_active": {"killswitch": False, "actiongate_rate": False, "system_degraded": False},
}


def _alert_state_path() -> Path:
    configured = settings.alert_state_path
    if configured:
        return Path(configured)
    return Path(settings.db_path).parent / "cron" / "alert_watch_state.json"


def _load_alert_state() -> Dict[str, Any]:
    path = _alert_state_path()
    if not path.exists():
        return json.loads(json.dumps(_ALERT_STATE_DEFAULT))
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return json.loads(json.dumps(_ALERT_STATE_DEFAULT))


def _save_alert_state(state: Dict[str, Any]) -> None:
    path = _alert_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd ~/projects/AI-Agents/msb-v3 && /opt/homebrew/Caskroom/miniforge/base/bin/python -m pytest tests/cron/test_actions.py -k alert_state -v`
Expected: 2 passed

- [ ] **Step 6: Commit**

```bash
cd ~/projects/AI-Agents/msb-v3
git add src/msb_v3/core/config.py src/msb_v3/cron/actions.py tests/cron/test_actions.py
git commit -m "feat(cron): alert-check state persistence helpers"
```

---

### Task 2: The `alert_check` action — rules, notification, registration

**Files:**
- Modify: `src/msb_v3/core/config.py` (add 5 fields, same block as Task 1's field)
- Modify: `src/msb_v3/cron/actions.py` (add `_send_hermes_alert`, `action_alert_check`, register in `ACTIONS`)
- Test: `tests/cron/test_actions.py` (append)

**Interfaces:**
- Consumes: `_load_alert_state()`, `_save_alert_state(state)` from Task 1. `_ok(summary, **detail)`, `_fail(summary, **detail)` already defined at the top of `actions.py`.
- Produces: `action_alert_check(params: Dict[str, Any]) -> Dict[str, Any]` — dispatchable via `run_action("alert_check", {})`. Task 3's `ensure_alert_check_job()` schedules this.

- [ ] **Step 1: Add the 5 remaining settings fields**

In `src/msb_v3/core/config.py`, right after the `alert_state_path` field added in Task 1, add:

```python
    alert_actiongate_threshold: int = field(default_factory=lambda: int(os.getenv("MSB_ALERT_ACTIONGATE_THRESHOLD", "5")))
    alert_actiongate_window_s: int = field(default_factory=lambda: int(os.getenv("MSB_ALERT_ACTIONGATE_WINDOW_S", "900")))
    alert_degraded_consecutive_threshold: int = field(default_factory=lambda: int(os.getenv("MSB_ALERT_DEGRADED_CONSECUTIVE", "2")))
    # `hermes send` (no LLM, no running gateway required for bot-token
    # platforms) — see ~/.local/bin/hermes send --help. Overridable in case
    # the server process's PATH doesn't include it.
    hermes_send_cmd: str = field(default_factory=lambda: os.getenv("MSB_HERMES_SEND_CMD", "hermes"))
    alert_telegram_target: str = field(default_factory=lambda: os.getenv("MSB_ALERT_TELEGRAM_TARGET", "telegram"))
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/cron/test_actions.py`:

```python
def _reset_actiongate_counter() -> None:
    from msb_v3.observability.metrics import ACTIONGATE_DECISIONS

    for verdict in ("failed", "denied"):
        ACTIONGATE_DECISIONS.labels(verdict=verdict)._value.set(0)


def test_alert_check_no_alert_when_healthy(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(actions.settings, "db_path", str(tmp_path / "msb.db"))
    monkeypatch.setattr(actions.settings, "alert_state_path", "")
    _reset_actiongate_counter()
    monkeypatch.setattr(
        "msb_v3.governance.killswitch.KillSwitch",
        lambda: type("KS", (), {"is_armed": lambda self: False})(),
    )
    monkeypatch.setattr("msb_v3.api.system.system_health", lambda: {"overall": "healthy"})
    sent = []
    monkeypatch.setattr(actions, "_send_hermes_alert", lambda msg: sent.append(msg) or True)

    result = actions.run_action("alert_check", {})

    assert result["ok"] is True
    assert sent == []


def test_alert_check_killswitch_armed_then_recovers(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(actions.settings, "db_path", str(tmp_path / "msb.db"))
    monkeypatch.setattr(actions.settings, "alert_state_path", "")
    _reset_actiongate_counter()
    monkeypatch.setattr("msb_v3.api.system.system_health", lambda: {"overall": "healthy"})
    sent = []
    monkeypatch.setattr(actions, "_send_hermes_alert", lambda msg: sent.append(msg) or True)

    monkeypatch.setattr(
        "msb_v3.governance.killswitch.KillSwitch",
        lambda: type("KS", (), {"is_armed": lambda self: True})(),
    )
    actions.run_action("alert_check", {})
    assert len(sent) == 1
    assert "ARMED" in sent[0]

    # Still armed on the next poll — must NOT re-alert (edge-triggered).
    actions.run_action("alert_check", {})
    assert len(sent) == 1

    monkeypatch.setattr(
        "msb_v3.governance.killswitch.KillSwitch",
        lambda: type("KS", (), {"is_armed": lambda self: False})(),
    )
    actions.run_action("alert_check", {})
    assert len(sent) == 2
    assert "RECOVERED" in sent[1]


def test_alert_check_actiongate_rate_spike(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from msb_v3.observability.metrics import ACTIONGATE_DECISIONS

    monkeypatch.setattr(actions.settings, "db_path", str(tmp_path / "msb.db"))
    monkeypatch.setattr(actions.settings, "alert_state_path", "")
    monkeypatch.setattr(actions.settings, "alert_actiongate_threshold", 5)
    monkeypatch.setattr(actions.settings, "alert_actiongate_window_s", 900)
    _reset_actiongate_counter()
    monkeypatch.setattr(
        "msb_v3.governance.killswitch.KillSwitch",
        lambda: type("KS", (), {"is_armed": lambda self: False})(),
    )
    monkeypatch.setattr("msb_v3.api.system.system_health", lambda: {"overall": "healthy"})
    sent = []
    monkeypatch.setattr(actions, "_send_hermes_alert", lambda msg: sent.append(msg) or True)

    # Establish the window baseline (0 failed/denied at this point).
    actions.run_action("alert_check", {})
    assert sent == []

    # 6 denials within the same window > threshold of 5.
    for _ in range(6):
        ACTIONGATE_DECISIONS.labels(verdict="denied").inc()
    actions.run_action("alert_check", {})
    assert len(sent) == 1
    assert "spike" in sent[0]


def test_alert_check_degraded_needs_two_consecutive_polls(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(actions.settings, "db_path", str(tmp_path / "msb.db"))
    monkeypatch.setattr(actions.settings, "alert_state_path", "")
    monkeypatch.setattr(actions.settings, "alert_degraded_consecutive_threshold", 2)
    _reset_actiongate_counter()
    monkeypatch.setattr(
        "msb_v3.governance.killswitch.KillSwitch",
        lambda: type("KS", (), {"is_armed": lambda self: False})(),
    )
    monkeypatch.setattr("msb_v3.api.system.system_health", lambda: {"overall": "degraded"})
    sent = []
    monkeypatch.setattr(actions, "_send_hermes_alert", lambda msg: sent.append(msg) or True)

    actions.run_action("alert_check", {})
    assert sent == []  # first degraded poll: not yet 2 consecutive

    actions.run_action("alert_check", {})
    assert len(sent) == 1
    assert "degraded" in sent[0]

    monkeypatch.setattr("msb_v3.api.system.system_health", lambda: {"overall": "healthy"})
    actions.run_action("alert_check", {})
    assert len(sent) == 2
    assert "RECOVERED" in sent[1]


def test_alert_check_survives_read_exception(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """A rule's read function raising must not crash the action, must not
    block the other rules, and must not corrupt the state file for the
    next poll."""
    monkeypatch.setattr(actions.settings, "db_path", str(tmp_path / "msb.db"))
    monkeypatch.setattr(actions.settings, "alert_state_path", "")
    _reset_actiongate_counter()

    def _raise_killswitch() -> None:
        raise RuntimeError("killswitch db locked")

    monkeypatch.setattr("msb_v3.governance.killswitch.KillSwitch", _raise_killswitch)
    monkeypatch.setattr("msb_v3.api.system.system_health", lambda: {"overall": "healthy"})
    monkeypatch.setattr(actions, "_send_hermes_alert", lambda msg: True)

    result = actions.run_action("alert_check", {})

    assert result["ok"] is False
    assert any("killswitch" in e for e in result["detail"]["errors"])

    # Next poll (killswitch reading fine again) must work normally, proving
    # the state file wasn't corrupted by the failed poll.
    monkeypatch.setattr(
        "msb_v3.governance.killswitch.KillSwitch",
        lambda: type("KS", (), {"is_armed": lambda self: False})(),
    )
    result2 = actions.run_action("alert_check", {})
    assert result2["ok"] is True


def test_alert_check_reports_send_failure(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(actions.settings, "db_path", str(tmp_path / "msb.db"))
    monkeypatch.setattr(actions.settings, "alert_state_path", "")
    _reset_actiongate_counter()
    monkeypatch.setattr(
        "msb_v3.governance.killswitch.KillSwitch",
        lambda: type("KS", (), {"is_armed": lambda self: True})(),
    )
    monkeypatch.setattr("msb_v3.api.system.system_health", lambda: {"overall": "healthy"})
    monkeypatch.setattr(actions, "_send_hermes_alert", lambda msg: False)

    result = actions.run_action("alert_check", {})

    assert result["ok"] is False
    assert result["detail"]["send_failures"]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd ~/projects/AI-Agents/msb-v3 && /opt/homebrew/Caskroom/miniforge/base/bin/python -m pytest tests/cron/test_actions.py -k alert_check -v`
Expected: FAIL — `run_action("alert_check", {})` returns the "unknown cron action type" failure (the action doesn't exist/isn't registered yet).

- [ ] **Step 4: Implement `_send_hermes_alert` and `action_alert_check`**

In `src/msb_v3/cron/actions.py`, add this right after the state helpers from Task 1 (still before `# --- registry ---`):

```python
def _send_hermes_alert(message: str) -> bool:
    """Send one alert via the Hermes agent's `send` CLI (no LLM, no running
    gateway required for bot-token platforms — see `hermes send --help`).
    Never raises: a missing binary or a timeout is a failure to report, not
    a crash."""
    cmd = [settings.hermes_send_cmd, "send", "-t", settings.alert_telegram_target, message]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def action_alert_check(params: Dict[str, Any]) -> Dict[str, Any]:
    """Watch killswitch state, ActionGate BLOCK/FAIL rate, and system health;
    notify (edge-triggered — once per state change, not once per poll) via
    Hermes when a rule crosses its threshold, and again on recovery."""
    from msb_v3.governance.killswitch import KillSwitch
    from msb_v3.observability.metrics import ACTIONGATE_DECISIONS
    from msb_v3.api.system import system_health

    state = _load_alert_state()
    notifications: List[str] = []
    errors: List[str] = []

    # Rule 1: killswitch armed.
    try:
        armed = KillSwitch().is_armed()
    except Exception as exc:  # noqa: BLE001 — one rule's read failure must not block the others
        armed = state["alerts_active"]["killswitch"]
        errors.append(f"killswitch read failed: {exc}")
    else:
        if armed and not state["alerts_active"]["killswitch"]:
            notifications.append(
                "🚨 MSB v3 ALERT: killswitch ARMED — execution halted. Investigate immediately."
            )
        elif not armed and state["alerts_active"]["killswitch"]:
            notifications.append("✅ MSB v3 RECOVERED: killswitch disarmed — execution resumed.")
    state["alerts_active"]["killswitch"] = armed

    # Rule 2: ActionGate BLOCK/FAIL rate, resetting window (simplest correct
    # implementation — no per-event history to store).
    try:
        failed = ACTIONGATE_DECISIONS.labels(verdict="failed")._value.get()
        denied = ACTIONGATE_DECISIONS.labels(verdict="denied")._value.get()
        now = datetime.now(timezone.utc)
        window_start_raw = state.get("actiongate_window_start")
        window_start = datetime.fromisoformat(window_start_raw) if window_start_raw else now
        if (now - window_start).total_seconds() >= settings.alert_actiongate_window_s:
            if state["alerts_active"]["actiongate_rate"]:
                notifications.append("✅ MSB v3 RECOVERED: ActionGate rate back to normal.")
            state["alerts_active"]["actiongate_rate"] = False
            state["actiongate_baseline"] = {"failed": failed, "denied": denied}
            state["actiongate_window_start"] = now.isoformat()
        else:
            baseline = state.get("actiongate_baseline", {"failed": failed, "denied": denied})
            delta = (failed - baseline.get("failed", failed)) + (denied - baseline.get("denied", denied))
            if delta > settings.alert_actiongate_threshold and not state["alerts_active"]["actiongate_rate"]:
                notifications.append(
                    f"🚨 MSB v3 ALERT: ActionGate spike — {int(delta)} denied/failed in window "
                    f"(threshold {settings.alert_actiongate_threshold}). Check /cockpit."
                )
                state["alerts_active"]["actiongate_rate"] = True
    except Exception as exc:  # noqa: BLE001
        errors.append(f"actiongate read failed: {exc}")

    # Rule 3: system degraded for 2+ consecutive polls (filters a single blip).
    try:
        health = system_health()
        overall = health.get("overall", "healthy")
    except Exception as exc:  # noqa: BLE001
        overall = "FAILED"
        errors.append(f"system_health call failed: {exc}")
    if overall in ("degraded", "FAILED"):
        state["consecutive_degraded"] = state.get("consecutive_degraded", 0) + 1
        if (
            state["consecutive_degraded"] >= settings.alert_degraded_consecutive_threshold
            and not state["alerts_active"]["system_degraded"]
        ):
            notifications.append(
                f"⚠️ MSB v3: system health degraded ({overall}) for "
                f"{state['consecutive_degraded']} consecutive checks. Check /system/health."
            )
            state["alerts_active"]["system_degraded"] = True
    else:
        if state["alerts_active"]["system_degraded"]:
            notifications.append("✅ MSB v3 RECOVERED: system health back to healthy.")
        state["consecutive_degraded"] = 0
        state["alerts_active"]["system_degraded"] = False

    send_failures: List[str] = []
    sent: List[str] = []
    for msg in notifications:
        if _send_hermes_alert(msg):
            sent.append(msg)
        else:
            send_failures.append(msg)

    _save_alert_state(state)

    detail = {"notifications_sent": sent, "send_failures": send_failures, "errors": errors}
    if errors or send_failures:
        return _fail("alert check encountered errors", **detail)
    summary = f"alert check: {len(sent)} notification(s)" if sent else "alert check: no change"
    return _ok(summary, **detail)
```

- [ ] **Step 5: Register the action**

In `src/msb_v3/cron/actions.py`, find:

```python
ACTIONS: Dict[str, ActionFn] = {
    "health_check": action_health_check,
    "audit_chain_verify": action_audit_chain_verify,
    "backup_spine": action_backup_spine,
    "metric_export": action_metric_export,
    "log_rotation": action_log_rotation,
    "http_call": action_http_call,
    "wake_agent": action_wake_agent,
}
```

Add `"alert_check": action_alert_check,` as the last entry.

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd ~/projects/AI-Agents/msb-v3 && /opt/homebrew/Caskroom/miniforge/base/bin/python -m pytest tests/cron/test_actions.py -k alert -v`
Expected: 8 passed (2 from Task 1, 6 from this task)

- [ ] **Step 7: Commit**

```bash
cd ~/projects/AI-Agents/msb-v3
git add src/msb_v3/core/config.py src/msb_v3/cron/actions.py tests/cron/test_actions.py
git commit -m "feat(cron): alert_check action — killswitch/ActionGate-rate/health-degradation rules"
```

---

### Task 3: Cron job seeding + app lifespan wiring

**Files:**
- Modify: `src/msb_v3/core/config.py` (add 2 fields)
- Modify: `src/msb_v3/cron/actions.py` (add `ensure_alert_check_job`)
- Modify: `src/msb_v3/api/app.py:88-93`
- Test: `tests/cron/test_actions.py` (append)

**Interfaces:**
- Consumes: `settings.alert_check_schedule` (new), `CronStore` from `msb_v3.cron.store` (constructor: `CronStore(db_path: str)`, methods `get_job(job_id) -> Dict`, `create_job(job_id, name, schedule, action, *, governance=None) -> Dict`, `list_jobs() -> List[Dict]` — all already exist, used exactly this way by `ensure_wake_job` in `src/msb_v3/wake/runner.py:196-220`).
- Produces: `ensure_alert_check_job(cron_store: Any = None) -> bool`, called from `app.py`'s lifespan.

- [ ] **Step 1: Add the 2 remaining settings fields**

In `src/msb_v3/core/config.py`, right after the `alert_telegram_target` field added in Task 2, add:

```python
    alert_check_enabled: bool = field(default_factory=lambda: os.getenv("MSB_ALERT_CHECK_ENABLED", "1") == "1")
    alert_check_schedule: str = field(default_factory=lambda: os.getenv("MSB_ALERT_CHECK_SCHEDULE", "*/5 * * * *"))
```

- [ ] **Step 2: Write the failing test**

Append to `tests/cron/test_actions.py`:

```python
def test_ensure_alert_check_job_seeds_once(tmp_path) -> None:
    from msb_v3.cron.store import CronStore

    cron_store = CronStore(db_path=str(tmp_path / "cron.db"))
    assert actions.ensure_alert_check_job(cron_store) is True
    job = cron_store.get_job("alert-check")
    assert job["action"]["type"] == "alert_check"
    assert job["schedule"] == "*/5 * * * *"
    # Idempotent — second call does not clobber.
    assert actions.ensure_alert_check_job(cron_store) is True
    assert len(cron_store.list_jobs()) == 1
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd ~/projects/AI-Agents/msb-v3 && /opt/homebrew/Caskroom/miniforge/base/bin/python -m pytest tests/cron/test_actions.py -k ensure_alert_check_job -v`
Expected: FAIL with `AttributeError: module 'msb_v3.cron.actions' has no attribute 'ensure_alert_check_job'`

- [ ] **Step 4: Implement `ensure_alert_check_job`**

In `src/msb_v3/cron/actions.py`, add this at the very end of the file (after the `ACTIONS` dict):

```python
def ensure_alert_check_job(cron_store: Any = None) -> bool:
    """Seed the alert-check cron job (schedule settings.alert_check_schedule)
    if missing. Idempotent — called from the app lifespan when
    alert_check_enabled and cron_enabled are both on."""
    from msb_v3.cron.store import CronStore

    store = cron_store if cron_store is not None else CronStore()
    try:
        store.get_job("alert-check")
        return True
    except KeyError:
        pass
    try:
        store.create_job(
            "alert-check",
            "Governed-loop alerting (killswitch / ActionGate rate / system health)",
            settings.alert_check_schedule,
            {"type": "alert_check", "params": {}},
            governance={"max_retries": 1, "timeout_s": 60.0, "notify_on_failure": False},
        )
        logger.info("seeded alert-check cron job (%s)", settings.alert_check_schedule)
        return True
    except ValueError:
        return False
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd ~/projects/AI-Agents/msb-v3 && /opt/homebrew/Caskroom/miniforge/base/bin/python -m pytest tests/cron/test_actions.py -k ensure_alert_check_job -v`
Expected: 1 passed

- [ ] **Step 6: Wire into app lifespan**

In `src/msb_v3/api/app.py`, find:

```python
        if settings.wake_enabled:
            from msb_v3.wake.runner import ensure_wake_job

            ensure_wake_job()
```

Add immediately after it (same indentation level, still inside `if settings.cron_enabled:`):

```python
        if settings.alert_check_enabled:
            from msb_v3.cron.actions import ensure_alert_check_job

            ensure_alert_check_job()
```

- [ ] **Step 7: Run the full cron test suite**

Run: `cd ~/projects/AI-Agents/msb-v3 && /opt/homebrew/Caskroom/miniforge/base/bin/python -m pytest tests/cron/ tests/api/ -q`
Expected: all pass, 0 failed (this confirms the app.py change didn't break server startup — several `tests/api/` tests boot the app via `TestClient(create_app())`)

- [ ] **Step 8: Commit**

```bash
cd ~/projects/AI-Agents/msb-v3
git add src/msb_v3/core/config.py src/msb_v3/cron/actions.py src/msb_v3/api/app.py tests/cron/test_actions.py
git commit -m "feat(cron): seed alert-check cron job on server startup"
```

---

### Task 4: Full verification pass

**Files:** none (verification only)

- [ ] **Step 1: Run mypy**

Run: `cd ~/projects/AI-Agents/msb-v3 && /opt/homebrew/Caskroom/miniforge/base/bin/python -m mypy src`
Expected: `Success: no issues found in N source files`

- [ ] **Step 2: Run ruff**

Run: `cd ~/projects/AI-Agents/msb-v3 && /opt/homebrew/Caskroom/miniforge/base/bin/python -m ruff check src/ tests/`
Expected: `All checks passed!`

- [ ] **Step 3: Run the full test suite**

Run: `cd ~/projects/AI-Agents/msb-v3 && /opt/homebrew/Caskroom/miniforge/base/bin/python -m pytest -q tests/`
Expected: all pass, 0 failed (this is the same suite the earlier CI/portability work tonight got to green — this task must not regress it)

- [ ] **Step 4: Manual smoke test against the real running server**

Run (with the real msb-v3 server already up on :8766):
```bash
cd ~/projects/AI-Agents/msb-v3
/opt/homebrew/Caskroom/miniforge/base/bin/python -c "
from msb_v3.cron.actions import run_action
result = run_action('alert_check', {})
print(result)
"
```
Expected: `{'ok': True, 'summary': 'alert check: no change', 'detail': {...}}` (assuming the live system is healthy and killswitch is disarmed, which it was as of tonight's session). This does NOT send a real Telegram message unless a rule is actually breached — safe to run.

- [ ] **Step 5: Confirm the job seeds on real server restart**

Run: `curl -s http://localhost:8766/cron/jobs 2>/dev/null | python3 -m json.tool | grep -A3 alert-check` (after restarting the server via `scripts/start.sh stop && scripts/start.sh start`, or waiting for the next natural restart) — confirms `ensure_alert_check_job()` actually ran during a real lifespan startup, not just in tests.

- [ ] **Step 6: Final commit if step 5 required a restart**

If restarting the server was needed to verify Step 5 and nothing else changed, no commit is needed — this step is verification only, not a code change.
