"""Tests for uac.observer_log.ObserverLog."""
from __future__ import annotations

from msb_v3.uac.observer_log import ObserverLog


def _log(tmp_path) -> ObserverLog:
    return ObserverLog(db_path=str(tmp_path / "observer.db"))


def test_narrate_and_read(tmp_path):
    log = _log(tmp_path)
    log.narrate("mission-1", "Research started.")
    log.narrate("mission-1", "Collecting regulations.")

    entries = log.read("mission-1")
    assert [e.message for e in entries] == ["Research started.", "Collecting regulations."]


def test_missions_are_isolated(tmp_path):
    log = _log(tmp_path)
    log.narrate("mission-1", "For mission 1.")
    log.narrate("mission-2", "For mission 2.")

    assert [e.message for e in log.read("mission-1")] == ["For mission 1."]
    assert [e.message for e in log.read("mission-2")] == ["For mission 2."]


def test_read_as_text_is_human_readable(tmp_path):
    log = _log(tmp_path)
    log.narrate("mission-1", "Research started.")
    text = log.read_as_text("mission-1")
    assert "Research started." in text
    assert text.startswith("[")  # timestamp bracket, per the blueprint's narration format


def test_read_empty_mission_returns_empty_list(tmp_path):
    log = _log(tmp_path)
    assert log.read("never-started") == []
