#!/usr/bin/env python3
"""Identity-shadow daily evidence step — the heartbeat K22 criterion 1 needs.

Criterion 1 is judged on RUNTIME-origin records, and a test run can only ever
produce "test" ones. So without a live caller the criterion has no source of
evidence and reads INSUFFICIENT DATA — the state it sat in until 2026-09-20.
This script is that caller: it runs the runtime probe against the live surfaces,
records the outcome *and* the judge's own criterion-1 verdict in the daily gate's
event log (which the gate commits and pushes), and asks for a notification when a
surface stops producing records.

EVIDENCE, NOT VERDICT — it never influences a gate's PASS/FAIL, and the gate
treats a non-zero exit here as a warning rather than a failure. Two measured
reasons: the /chat half depends on an 8B model choosing to call a tool, so a
single miss is not a regression; and the bridge half performs a REAL vault write
(99_Meta/identity-shadow-probe.md), which a gate must not escalate on.

What it does do is keep the evidence from rotting silently. Recorded records
persist in the corpus, so criterion 1 keeps reading MET from older evidence even
after the live path stops working — accumulation is what makes a *drop* visible.
A surface that misses TWICE IN A ROW raises a notification, because that is where
"the model didn't feel like it" stops being the likely explanation.

A surface can also be silent ON PURPOSE, and the two must not look alike. The
bridge's identity observation point sits after its capability gate, so with no
MSB_MCP_GRANTED_CAPABILITIES grant the bridge is refused before the recorder runs
and can produce nothing — permanently, by configuration. Reporting that as a
"miss" would nag forever about a state someone chose; reporting it as a healthy
run would be a lie. So each run classifies every surface as one of:

    on            a record was produced today
    off-declared  silent, and the configuration says so (no grant) — expected
    drift         the config declaration and the running server AGREE ON
                  NOTHING — the process is out of step with .env, so it needs a
                  restart rather than a diagnosis
    miss          silent for a reason nobody declared — the only state that
                  escalates to a notification

Drift is checked in BOTH directions, because the two are equally false: a file
that declares the grant while the server refuses it, and a file that declares
read-only while the server executes a write. Declared-off runs are skipped by
the streak rather than counted as misses — otherwise withdrawing a grant for a
week and restoring it would alert on the first day back, for a week of silence
that was never a failure.

Usage:
    identity_shadow_daily.py [--alert-file FILE] [--events-log PATH]
                             [--probe-json FILE] [--status-json FILE]

--probe-json / --status-json skip the live calls and read an existing capture,
which is what makes the logic testable without a server or a model.

Exit codes:
    0 = an event was recorded — whatever the surfaces did (read the event)
    1 = the evidence step itself failed (could not run the probe or the judge,
        or could not write the event log)
    2 = usage error
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from msb_v3.governance.identity_shadow import LIVE_SURFACES  # noqa: E402

PROBE = REPO_ROOT / "scripts" / "probe_identity_shadow_runtime.py"
DEFAULT_EVENTS_LOG = REPO_ROOT / "artifacts" / "hygiene" / "daily_gate_events.jsonl"
EVENT_NAME = "identity_shadow_probe"

# The one surface whose silence can be a declared state: its governed tools are
# the 5 vault mutations (all `vault.write`) and the recorder sits after the
# capability gate, so withdrawing the grant makes it structurally unobservable.
BRIDGE_SURFACE = "mcp-bridge"
GRANT_KEY = "MSB_MCP_GRANTED_CAPABILITIES"
VAULT_WRITE = "vault.write"

# What each per-surface state means for the streak and the alert.
STATE_ON = "on"
STATE_OFF = "off-declared"
STATE_DRIFT = "drift"
STATE_MISS = "miss"

# How many consecutive runs with no record before this asks for a notification.
# 1 would fire on ordinary model flakiness; 3 delays the signal by two days.
SUSTAINED_MISS_RUNS = 2


def _read_json(path: Path) -> Dict[str, Any]:
    """Parse a capture, or {} — an unreadable capture is a recorded fact, not a crash."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def declared_grant() -> Tuple[bool, str]:
    """Does the CONFIGURATION declare the bridge's `vault.write` grant?

    Read from .env first, because that is the file the running server sourced at
    import time and therefore the best available statement of what it was
    configured with; the process environment is the fallback for a machine that
    configures the service another way.

    This is a declaration, not the truth. The probe reports the truth (it talks
    to the running server). Keeping them separate is the whole point — the
    comparison is what detects a server that has not been restarted since .env
    changed, which is a silent failure mode otherwise.
    """
    raw: Optional[str] = None
    source = "absent"
    try:
        for line in (REPO_ROOT / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{GRANT_KEY}="):
                raw = line.split("=", 1)[1].strip().strip("\"").strip("'")
                source = ".env"
                break
    except OSError:
        pass
    if raw is None:
        env_value = os.environ.get(GRANT_KEY)
        if env_value is not None:
            raw, source = env_value, "environment"
    granted = VAULT_WRITE in {c.strip() for c in (raw or "").split(",") if c.strip()}
    return granted, source


def surface_state(surface: str, produced: int, blocked: str, granted: bool) -> str:
    """What today's outcome means for one surface. Four states, not two."""
    if produced:
        # A bridge record while the config declares it read-only means the
        # RUNNING server still holds a withdrawn grant: a restart, not a
        # surface event. (It is also the shape an unnoticed widening takes.)
        if surface == BRIDGE_SURFACE and not granted:
            return STATE_DRIFT
        return STATE_ON
    if surface != BRIDGE_SURFACE:
        return STATE_MISS
    if blocked != "capability-denied":
        # Silent without the gate's refusal to point at: nothing here says the
        # silence was chosen, so it stays a miss and keeps escalating.
        return STATE_MISS
    # Refused at the capability gate. Expected only if the config says so.
    return STATE_OFF if not granted else STATE_DRIFT


def load_events(path: Path) -> List[Dict[str, Any]]:
    """Every identity_shadow_probe event already recorded, oldest first.

    Unparseable lines are skipped rather than raised, and an unreadable log yields
    an empty history: this reads a log the gate itself writes and appends to, and
    refusing to record today's evidence because an older line is torn would make
    the missing-evidence problem worse. Recording is the job; the history only
    sharpens the streak. The tradeoff is explicit — an unreadable log resets the
    streak to 1, so it suppresses an alert rather than inventing one. That state
    is loud anyway: the append then fails, the step exits non-zero, and the gate
    logs it.
    """
    target = Path(path)
    try:
        if not target.exists():
            return []
        text = target.read_text(encoding="utf-8")
    except OSError:
        return []
    events: List[Dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and record.get("event") == EVENT_NAME:
            events.append(record)
    return events


def streak_for(events: List[Dict[str, Any]], surface: str, produced_today: int) -> int:
    """Consecutive runs, including today, that produced no record for this surface.

    A prior run that did not mention this surface *at all* ends the streak rather
    than extending it: "not recorded" is unknown, and unknown must not decay into
    failure — the same reading this lane uses everywhere else. (A run that
    mentioned the surface with 0 records is a genuine miss and does extend it.)
    """
    if produced_today:
        return 0
    streak = 1
    for record in reversed(events):
        if (record.get("surface_state") or {}).get(surface) == STATE_OFF:
            # Declared-off is neither evidence nor a miss, so it is skipped
            # rather than counted: a grant withdrawn for a week and restored
            # must not alert on the first day back about a week of intended
            # silence. Skipping (not breaking) also keeps a genuinely older
            # streak alive across the quiet period.
            continue
        if (record.get("new_records") or {}).get(surface) == 0:
            streak += 1
        else:
            break
    return streak


def build_event(
    probe: Dict[str, Any],
    status: Dict[str, Any],
    *,
    ts: str,
    declared: Tuple[bool, str] = (False, "absent"),
) -> Dict[str, Any]:
    """The event this run contributes. One row per live surface, always present.

    Carries the DECLARATION beside the OBSERVATION (and the judge's verdict
    beside the probe's counts), so a later reader can tell a quiet-by-choice run
    from a broken one without re-deriving either from today's environment.
    """
    tallies = probe.get("surfaces") or {}
    granted, source = declared
    new_records = {s: int((tallies.get(s) or {}).get("records") or 0) for s in LIVE_SURFACES}
    return {
        "ts": ts,
        "event": EVENT_NAME,
        "probe_ok": bool(probe),
        "new_records": new_records,
        "with_actor": {s: int((tallies.get(s) or {}).get("with_actor") or 0) for s in LIVE_SURFACES},
        "declared": {"vault_write_granted": bool(granted), "source": source},
        "surface_state": {
            s: surface_state(s, new_records[s], str((tallies.get(s) or {}).get("blocked") or ""), granted)
            for s in LIVE_SURFACES
        },
        "runtime_records": status.get("runtime_records"),
        "criterion_1": next(
            (str(c.get("status") or "") for c in (status.get("criteria") or []) if c.get("id") == 1),
            "",
        ),
    }


def summarise(
    event: Dict[str, Any], history: List[Dict[str, Any]]
) -> Tuple[List[str], str, Dict[str, int]]:
    """Human lines, the notification text ("" when none is due), and the streaks.

    Deliberately no double quotes and no em dash in the alert text: the gate
    interpolates it into an AppleScript string, and prose that can break the
    alerting channel is worse than plain prose.
    """
    lines = [
        "identity-shadow: new records "
        f"{event['new_records']} (criterion 1: {event['criterion_1'] or 'UNKNOWN'}, "
        f"runtime records in the corpus: {event['runtime_records']})"
    ]
    if not event["probe_ok"]:
        lines.append("WARN: identity-shadow probe produced no JSON - see this job's log")

    states = event.get("surface_state") or {}
    streaks: Dict[str, int] = {}
    sustained: List[str] = []
    for surface in LIVE_SURFACES:
        produced = int(event["new_records"].get(surface, 0))
        state = str(states.get(surface) or STATE_MISS)
        streaks[surface] = streak_for(history, surface, produced)
        if state == STATE_ON:
            continue
        if state == STATE_OFF:
            lines.append(
                f"identity-shadow: {surface} produced no record - observation is OFF BY DECLARATION "
                f"(no {VAULT_WRITE} grant in {GRANT_KEY}), so this is expected and is not a miss"
            )
            continue
        if state == STATE_DRIFT:
            lines.append(
                "WARN: identity-shadow: "
                f"{surface} contradicts the declared configuration (streak {streaks[surface]}) - "
                ".env and the running server disagree, which is what an un-restarted service "
                "looks like; restart it to apply .env"
            )
        else:
            lines.append(f"WARN: identity-shadow produced no new record on {surface} (streak {streaks[surface]})")
        # Drift is diagnosed, not excused: while it persists the surface is
        # producing no evidence either, so it escalates on the same rule.
        if streaks[surface] >= SUSTAINED_MISS_RUNS:
            sustained.append(f"{surface} ({streaks[surface]} runs)")

    alert = ""
    if sustained:
        alert = (
            "identity-shadow: no runtime record from "
            + ", ".join(sustained)
            + f" - criterion 1 is still reported from {event['runtime_records']} older record(s),"
            " so the measurement may be stale"
        )
    return lines, alert, streaks


def _run_json(cmd: List[str]) -> Dict[str, Any]:
    """Run a helper and parse its stdout. An empty or unparseable stdout yields {}.

    A non-zero exit with usable stdout still parses: the probe exits 0 by design,
    but a degraded run that still printed its report is evidence, and discarding
    it would understate the corpus the way this lane keeps insisting it must not.
    """
    env = dict(os.environ)
    parts = [str(REPO_ROOT / "src"), env.get("PYTHONPATH", "")]
    env["PYTHONPATH"] = os.pathsep.join(p for p in parts if p)
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if proc.stderr.strip():
        # The child's diagnostics belong in the gate's log, not in a swallowed pipe.
        print(proc.stderr.rstrip(), file=sys.stderr)
    if not proc.stdout.strip():
        return {}
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--events-log", type=Path, default=DEFAULT_EVENTS_LOG)
    parser.add_argument("--alert-file", type=Path, default=None, help="write the notification text here")
    parser.add_argument("--probe-json", type=Path, default=None, help="reuse a probe capture instead of running it")
    parser.add_argument("--status-json", type=Path, default=None, help="reuse a judge capture instead of running it")
    args = parser.parse_args(argv)

    ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    probe = _read_json(args.probe_json) if args.probe_json else _run_json([sys.executable, str(PROBE), "--json"])
    status = (
        _read_json(args.status_json)
        if args.status_json
        else _run_json([sys.executable, "-m", "msb_v3.governance", "identity-status", "--json"])
    )

    history = load_events(args.events_log)
    declared = declared_grant()
    event = build_event(probe, status, ts=ts, declared=declared)
    lines, alert, _ = summarise(event, history)

    try:
        with Path(args.events_log).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event) + "\n")
    except OSError as exc:
        print(f"identity-shadow: cannot write the event log ({exc})", file=sys.stderr)
        return 1

    for line in lines:
        print(line)
    if alert:
        print(f"ALERT: {alert}")
        if args.alert_file is not None:
            try:
                Path(args.alert_file).write_text(alert, encoding="utf-8")
            except OSError as exc:
                print(f"identity-shadow: cannot write the alert file ({exc})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
