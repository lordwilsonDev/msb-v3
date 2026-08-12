"""Charger tests — the stub brain is deterministic and UIM-compatible."""

from __future__ import annotations

from msb_v3.flywheel.chargers import StubCharger, StubScanner


def test_stub_charger_uim_shape() -> None:
    uim = StubCharger().charge("Sovereign memory consolidation", "sovereign-memory")
    assert uim["topic"] == "Sovereign memory consolidation"
    assert uim["slug"] == "sovereign-memory"
    assert uim["ok"] is True
    phase = uim["phase1"]
    assert phase["assumption"]
    assert phase["inversion"]
    assert len(phase["predictions"]) == 3


def test_stub_charger_deterministic() -> None:
    charger = StubCharger()
    a = charger.charge("Same problem statement", "same-slug")
    b = charger.charge("Same problem statement", "same-slug")
    assert a == b


def test_stub_charger_differs_across_problems() -> None:
    charger = StubCharger()
    a = charger.charge("Problem A", "a")
    b = charger.charge("Problem B", "b")
    assert a["phase1"]["inversion"] != b["phase1"]["inversion"]


def test_stub_scanner_is_honest() -> None:
    uim = StubCharger().charge("Scan me", "scan-me")
    result = StubScanner().scan("Scan me", uim)
    assert result["papers_scanned"] == 0  # never fakes a scan
    assert "stub" in result["notes"]
    assert len(result["candidates"]) >= 1
