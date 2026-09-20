"""The identity-shadow runtime probe self-tests itself.

The probe is the only thing in the repo that produces runtime-origin evidence,
so its failure mode is the dangerous one: reporting zero records when the
surfaces were never really exercised would read as a finding about the surfaces.
This wrapper runs the probe's in-process self-test in the standing suite (no
server, no model spend), the same way tests/test_probe_self_test.py does for the
conversation harness.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# parents[2]: this file sits in tests/governance/, one level below the
# tests/ files that get away with parents[1] (same as tests/docs/test_surface_map.py).
# Getting this wrong is silent in a full-suite run — an earlier-collected test
# module has already put scripts/ on sys.path by then — and only shows up when
# this file is run on its own.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import probe_identity_shadow_runtime as probe  # noqa: E402


def test_probe_self_test_passes():
    assert probe.self_test() == 0


def test_every_live_surface_has_an_exercise():
    """If a surface is added to LIVE_SURFACES, the probe must gain an exercise
    for it, or that surface stays unmeasurable while the probe still exits 0."""
    assert set(probe._EXERCISES) == set(probe.LIVE_SURFACES)


def test_tally_is_not_vacuous_and_does_not_cross_surfaces():
    rows = [
        {"surface": "mcp-bridge", "actor_supplied": True, "actor_id": "b"},
        {"surface": "chat", "actor_supplied": False},
    ]
    assert probe._tally("chat", rows)["records"] == 1
    assert probe._tally("chat", rows)["with_actor"] == 0
    assert probe._tally("mcp-bridge", rows)["records"] == 1
    assert probe._tally("governed-loop", rows)["records"] == 0


@pytest.mark.parametrize("surface", probe.LIVE_SURFACES)
def test_diagnosis_explains_an_empty_surface(surface: str):
    """An empty run must come with the mechanism, not just a zero — otherwise
    the probe's output cannot distinguish "the surface refused" from "the probe
    is broken"."""
    exercise = {"surface": surface, "status": 200, "evidence": "e"}
    assert probe._diagnose(surface, probe._tally(surface, []), exercise)
    # and it must stay silent once the surface has produced records
    assert probe._diagnose(surface, probe._tally(surface, [{"surface": surface}]), exercise) == []
