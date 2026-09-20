"""The daily identity-shadow evidence step — logic and wiring.

This step is the only thing that keeps criterion 1 from silently rotting: it
records runtime evidence every day and raises a notification when a surface stops
producing records. Its failure modes are therefore quiet ones — recording nothing,
or never noticing — so the mechanics are pinned here rather than left to a daily
job nobody watches. No server, no model: the live calls are replaced by captures.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# parents[2]: this file sits in tests/governance/, one level below the tests/
# files that get away with parents[1]. Getting it wrong is invisible in a full
# suite run, because an earlier-collected module has already put scripts/ on
# sys.path by then — see the JOB-020 notes.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import identity_shadow_daily as daily  # noqa: E402

LIVE = tuple(daily.LIVE_SURFACES)


def _probe(chat: int = 1, bridge: int = 1, with_actor: bool = True, bridge_blocked: str = "") -> dict:
    counts = {"chat": chat, "mcp-bridge": bridge}
    surfaces = {
        s: {"records": n, "with_actor": n if with_actor else 0, "actor_ids": [], "blocked": ""}
        for s, n in counts.items()
    }
    surfaces["mcp-bridge"]["blocked"] = bridge_blocked
    return {"surfaces": surfaces}


def _status(criterion_1: str = "MET", runtime_records: int = 2) -> dict:
    return {
        "criteria": [{"id": 1, "name": "both live surfaces pass an actor", "status": criterion_1}],
        "runtime_records": runtime_records,
    }


def _event(
    chat: int = 1,
    bridge: int = 1,
    ts: str = "2026-09-21T06:15:00Z",
    declared: tuple = (True, ".env"),
    bridge_blocked: str = "",
) -> dict:
    """An event as if the grant were configured (the pre-revert world).

    `declared` defaults to GRANTED so the long-standing expectations below keep
    their meaning; the declared-off cases state it explicitly.
    """
    return daily.build_event(
        _probe(chat, bridge, bridge_blocked=bridge_blocked), _status(), ts=ts, declared=declared
    )


@pytest.fixture
def declared_granted(monkeypatch):
    """Pin the declaration for the end-to-end runs.

    main() reads the real repo .env, which is edited by hand and by
    scripts/set-identity-shadow-rollout.sh — so without this, these tests would
    silently change meaning when this machine's configuration does.
    """
    monkeypatch.setattr(daily, "declared_grant", lambda: (True, ".env"))


# --- the event records what actually happened -------------------------------


def test_event_carries_the_judges_verdict_not_its_own():
    event = daily.build_event(_probe(), _status("INSUFFICIENT DATA", runtime_records=0), ts="t")
    assert event["criterion_1"] == "INSUFFICIENT DATA"
    assert event["runtime_records"] == 0
    # the judge's word, taken from the judge's own output — the step does not
    # decide a criterion, it records one
    assert event["event"] == "identity_shadow_probe"


def test_event_always_carries_every_live_surface():
    """A missing key would read as "unknown" forever; 0 is a real measurement."""
    event = daily.build_event({}, {}, ts="t")
    assert set(event["new_records"]) == set(LIVE)
    assert set(event["with_actor"]) == set(LIVE)
    assert event["probe_ok"] is False
    assert all(v == 0 for v in event["new_records"].values())


def test_event_maps_probe_tallies_per_surface():
    event = daily.build_event(_probe(chat=1, bridge=0, with_actor=False), _status(), ts="t")
    assert event["new_records"][LIVE[0]] == 1
    assert event["new_records"][LIVE[1]] == 0
    assert all(v == 0 for v in event["with_actor"].values())


# --- streaks: a miss must be distinguished from an unknown -------------------


@pytest.mark.parametrize("surface", LIVE)
def test_streak_is_zero_when_the_surface_produced(surface: str):
    assert daily.streak_for([_event(chat=0, bridge=0)], surface, 1) == 0


@pytest.mark.parametrize("surface", LIVE)
def test_streak_counts_consecutive_misses_today_included(surface: str):
    history = [_event(chat=0, bridge=0), _event(chat=0, bridge=0)]
    assert daily.streak_for(history, surface, 0) == 3


@pytest.mark.parametrize("surface", LIVE)
def test_streak_breaks_on_a_run_that_produced(surface: str):
    history = [_event(chat=0, bridge=0), _event(chat=1, bridge=1)]
    assert daily.streak_for(history, surface, 0) == 1


@pytest.mark.parametrize("surface", LIVE)
def test_an_absent_surface_ends_the_streak_rather_than_being_a_miss(surface: str):
    """A run that never recorded this surface is UNKNOWN, not failed — unknown
    must not decay into failure. (An older event schema, or a smaller surface
    set, must not manufacture a sustained-miss alert.)"""
    history = [{"event": "identity_shadow_probe", "ts": "old", "new_records": {"something-else": 0}}]
    assert daily.streak_for(history, surface, 0) == 1


# --- the notification is the whole point of running it daily -----------------


@pytest.mark.parametrize("surface", LIVE)
def test_first_miss_warns_but_does_not_notify(surface: str):
    lines, alert, _ = daily.summarise(_event(chat=0, bridge=0), [])
    assert alert == "", "one miss is model flakiness, not a regression"
    assert any("WARN" in ln and surface in ln for ln in lines)


@pytest.mark.parametrize("surface", LIVE)
def test_second_consecutive_miss_notifies(surface: str):
    lines, alert, streaks = daily.summarise(_event(chat=0, bridge=0), [_event(chat=0, bridge=0)])
    assert streaks[surface] == 2
    assert surface in alert
    assert "stale" in alert, "the alert must say the criterion is held up by older records"
    assert any("ALERT" not in ln and "WARN" in ln for ln in lines)


def test_alerts_are_plain_prose_the_notification_channel_cannot_break():
    """The gate interpolates this into an AppleScript string; a double quote
    would break the channel that reports the problem."""
    _, alert, _ = daily.summarise(_event(chat=0, bridge=0), [_event(chat=0, bridge=0)])
    assert '"' not in alert
    assert "—" not in alert


def test_a_healthy_run_is_silent():
    lines, alert, _ = daily.summarise(_event(), [])
    assert alert == ""
    assert not any("WARN" in ln for ln in lines)
    assert any("criterion 1: MET" in ln for ln in lines)


# --- a surface can be silent ON PURPOSE ------------------------------------
#
# The bridge's identity observation point sits after its capability gate, so
# withdrawing the vault.write grant makes it structurally unobservable. That is a
# legitimate configuration, and it must not be reported as a failure — nor as a
# healthy run.


OFF = (False, ".env")


def _off_event(chat: int = 1) -> dict:
    """The bridge refused at the capability gate, with no grant declared."""
    return _event(chat=chat, bridge=0, ts="2026-09-21T06:15:00Z", declared=OFF, bridge_blocked="capability-denied")


def test_a_withdrawn_grant_reads_as_off_by_declaration_not_a_miss():
    event = _off_event()
    assert event["surface_state"]["mcp-bridge"] == "off-declared"
    # the declaration is recorded beside the observation, so a later reader can
    # tell a quiet-by-choice run from a broken one without re-deriving it
    assert event["declared"] == {"vault_write_granted": False, "source": ".env"}


def test_declared_off_is_reported_and_never_notifies():
    lines, alert, _ = daily.summarise(_off_event(), [_event(), _event()])
    assert alert == ""
    assert any("OFF BY DECLARATION" in ln for ln in lines)
    assert not any("WARN" in ln for ln in lines), "an expected silence is not a warning"


def test_a_long_declared_off_stretch_never_alerts():
    off = _off_event()
    _, alert, streaks = daily.summarise(off, [off] * 10)
    assert alert == ""
    assert streaks["mcp-bridge"] == 1, "declared-off runs are skipped, not counted as misses"


def test_restoring_the_grant_does_not_alert_about_the_quiet_stretch():
    """The false alarm this prevents: withdraw the grant for a week, restore it,
    and day one notifies about a week of intended, declared silence."""
    today = _event(bridge=0)  # granted again, but silent for some other reason
    _, alert, streaks = daily.summarise(today, [_off_event(), _off_event(), _off_event()])
    assert today["surface_state"]["mcp-bridge"] == "miss"
    assert streaks["mcp-bridge"] == 1, "the quiet stretch must not carry over"
    assert alert == ""


def test_declared_off_history_does_not_mask_an_older_real_miss():
    """Skipping is not breaking: a genuine miss from BEFORE the grant was
    withdrawn is still part of the streak when the surface goes wrong again."""
    history = [_event(bridge=0), _off_event(), _off_event()]
    assert daily.streak_for(history, "mcp-bridge", 0) == 2


def test_config_and_the_running_server_disagreeing_is_drift_in_both_directions():
    # .env grants the capability, the running server refuses it: an un-restarted
    # service, not a surface regression.
    denied = _event(bridge=0, declared=(True, ".env"), bridge_blocked="capability-denied")
    assert denied["surface_state"]["mcp-bridge"] == "drift"
    lines, alert, _ = daily.summarise(denied, [])
    assert any("restart" in ln for ln in lines)
    assert alert == "", "drift names its own cause; it does not need the stale-evidence alert"

    # .env says read-only, yet the bridge executed a write — the withdrawn grant
    # is still live in the running server. The reverse direction, equally false.
    executed = _event(bridge=1, declared=OFF)
    assert executed["surface_state"]["mcp-bridge"] == "drift"
    lines, _, _ = daily.summarise(executed, [])
    assert any("WARN" in ln and "contradicts" in ln for ln in lines)


def test_drift_still_escalates_because_evidence_has_stopped():
    """Drift is diagnosed, not excused: while it persists no bridge evidence is
    being produced, so it keeps building the streak and eventually notifies."""
    drift = _event(bridge=0, declared=(True, ".env"), bridge_blocked="capability-denied")
    _, alert, streaks = daily.summarise(drift, [drift])
    assert streaks["mcp-bridge"] == 2
    assert "mcp-bridge" in alert


def test_silence_without_the_gates_refusal_stays_a_miss():
    """No capability denial to point at means nothing declared this silence, so
    it must keep behaving like the regression it probably is."""
    event = _event(bridge=0, declared=OFF, bridge_blocked="")
    assert event["surface_state"]["mcp-bridge"] == "miss"
    _, alert, _ = daily.summarise(event, [event])
    assert "mcp-bridge" in alert


# --- reading the declaration -------------------------------------------------


def test_declared_grant_comes_from_the_env_file_the_server_read(tmp_path, monkeypatch):
    monkeypatch.setattr(daily, "REPO_ROOT", tmp_path)
    (tmp_path / ".env").write_text(f"{daily.GRANT_KEY}=vault.write\n")
    assert daily.declared_grant() == (True, ".env")
    (tmp_path / ".env").write_text(f"{daily.GRANT_KEY}=\n")
    assert daily.declared_grant() == (False, ".env")


def test_declared_grant_needs_the_capability_as_a_member_not_a_substring(tmp_path, monkeypatch):
    """memory.write is a different capability and vault.writeX is not it; a
    substring test would read both as granting the vault."""
    monkeypatch.setattr(daily, "REPO_ROOT", tmp_path)
    for raw, expected in [
        ("memory.write", False),
        ("vault.writeX", False),
        ("vault.write", True),
        ("memory.write,vault.write", True),
        (" memory.write , vault.write ", True),
    ]:
        (tmp_path / ".env").write_text(f"{daily.GRANT_KEY}={raw}\n")
        assert daily.declared_grant() == (expected, ".env"), raw


def test_declared_grant_falls_back_to_the_environment_then_to_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(daily, "REPO_ROOT", tmp_path)  # no .env in this root
    monkeypatch.setenv(daily.GRANT_KEY, "vault.write")
    assert daily.declared_grant() == (True, "environment")
    monkeypatch.delenv(daily.GRANT_KEY)
    assert daily.declared_grant() == (False, "absent")


# --- end to end, no server, no model ---------------------------------------


def _captures(tmp_path: Path, probe: dict, status: dict) -> tuple[Path, Path]:
    p = tmp_path / "probe.json"
    s = tmp_path / "status.json"
    p.write_text(json.dumps(probe))
    s.write_text(json.dumps(status))
    return p, s


def test_main_records_exactly_one_event_and_exits_zero(tmp_path, capsys, declared_granted):
    probe, status = _captures(tmp_path, _probe(), _status())
    events = tmp_path / "events.jsonl"

    rc = daily.main(["--events-log", str(events), "--probe-json", str(probe), "--status-json", str(status)])

    assert rc == 0
    rows = [json.loads(ln) for ln in events.read_text().splitlines() if ln.strip()]
    assert len(rows) == 1
    assert rows[0]["event"] == "identity_shadow_probe"
    assert rows[0]["criterion_1"] == "MET"
    assert "criterion 1: MET" in capsys.readouterr().out


def test_main_appends_without_disturbing_the_gates_own_events(tmp_path, declared_granted):
    """The step appends to the daily gate's log, which carries other events."""
    events = tmp_path / "events.jsonl"
    events.write_text('{"ts": "t0", "event": "gate_run", "verdict": "PASS"}\n')
    probe, status = _captures(tmp_path, _probe(), _status())

    daily.main(["--events-log", str(events), "--probe-json", str(probe), "--status-json", str(status)])

    rows = [json.loads(ln) for ln in events.read_text().splitlines() if ln.strip()]
    assert [r["event"] for r in rows] == ["gate_run", "identity_shadow_probe"]


def test_main_detects_a_sustained_miss_across_days(tmp_path, declared_granted):
    """Two runs against the same log: the second one — and only the second —
    asks for a notification. This is the behaviour the whole daily job exists
    for, so it is exercised across two real invocations, not just summarise()."""
    events = tmp_path / "events.jsonl"
    probe, status = _captures(tmp_path, _probe(chat=0, bridge=0), _status())
    alert = tmp_path / "alert.txt"
    args = ["--events-log", str(events), "--probe-json", str(probe), "--status-json", str(status), "--alert-file", str(alert)]

    assert daily.main(args) == 0
    assert not alert.exists() or alert.read_text() == ""

    assert daily.main(args) == 0
    text = alert.read_text()
    assert LIVE[0] in text and "2 runs" in text


def test_main_fails_loudly_when_it_cannot_record(tmp_path, capsys, declared_granted):
    """Recording is the job; if it cannot record, the gate must be able to tell."""
    probe, status = _captures(tmp_path, _probe(), _status())
    rc = daily.main(["--events-log", str(tmp_path), "--probe-json", str(probe), "--status-json", str(status)])
    assert rc == 1
    assert "cannot write the event log" in capsys.readouterr().err


def test_main_reports_a_probe_that_produced_nothing(tmp_path, capsys, declared_granted):
    """An empty capture is a recorded fact, not an exception: the run still
    writes an event, and it still says the probe produced nothing."""
    status = tmp_path / "status.json"
    status.write_text(json.dumps(_status()))
    empty = tmp_path / "empty.json"
    empty.write_text("not json")
    events = tmp_path / "events.jsonl"

    rc = daily.main(["--events-log", str(events), "--probe-json", str(empty), "--status-json", str(status)])

    assert rc == 0
    row = json.loads(events.read_text().strip())
    assert row["probe_ok"] is False
    out = capsys.readouterr().out
    assert "no JSON" in out
    assert "WARN" in out


def test_wired_into_the_daily_gate_as_evidence_not_verdict():
    """The gate must invoke this step and must NOT let it change PASS/FAIL: its
    chat half depends on a model and its bridge half writes to the vault."""
    gate = (ROOT / "scripts" / "factory_gate_daily.sh").read_text()
    assert "identity_shadow_daily.py" in gate
    # invoked before the factory runs, so evidence accrues even if the factory fails
    assert gate.index("identity_shadow_daily.py") < gate.index("running factory gate")
    # a non-zero exit here is logged as a warning, never escalated into the verdict
    assert "gate verdict unaffected" in gate
