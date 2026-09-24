"""The BLE001 backlog gate is a claim about a config file, so it gets its own gate.

Three things are tested, and they are different in kind:

* **The gate is green.** `scripts/ble001_backlog.py` reconciles the declared
  `per-file-ignores` rows against the ledger and against live measurement. If it
  is red, the backlog and its written reasons have diverged — the config is
  wrong, not the test.
* **The gate can fail.** A gate that only ever passes is ceremony. Each check
  gets an injected case that must produce a finding: an unexplained row (the
  silent re-addition this exists for), a stale row, growth past the ceiling, a
  reasonless group, an unargued class exemption, an undeclared violating file,
  and a disarmed rule.
* **Its premise holds.** The whole gate rests on one mechanism: `ruff check
  --isolated` reveals what `per-file-ignores` hides. If that ever stops being
  true, every staleness finding becomes a false positive and the gate fails
  loudly for the wrong reason — so the premise is pinned directly, in both
  directions, rather than trusted.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "ble001_backlog.py"
RULE = "BLE001"


def _load_gate():
    spec = importlib.util.spec_from_file_location("ble001_backlog", str(SCRIPT))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["ble001_backlog"] = module
    spec.loader.exec_module(module)
    return module


bl = _load_gate()


@pytest.fixture(scope="module")
def gate():
    """One whole run, shared: the measurement shells out to ruff."""
    return bl.run()


@pytest.fixture(scope="module")
def live():
    return bl.live_sites()


def _ceiling(files: int, sites: int) -> dict:
    return {"files": files, "sites": sites}


# ---------------------------------------------------------------------------
# The gate: green on the tree as it stands
# ---------------------------------------------------------------------------


def test_gate_is_green(gate):
    assert gate.findings == [], "BLE001 backlog findings:\n  - " + "\n  - ".join(gate.findings)


def test_gate_reports_what_it_did_read(gate):
    """A gate that checks nothing must not read as a pass."""
    assert gate.stats["rule armed"] == 1
    assert gate.stats["declared files"] > 0
    assert gate.stats["covered sites"] > 0
    assert gate.stats["groups"] > 0
    assert gate.stats["class exemptions"] > 0


def test_every_declared_row_is_explained_and_every_explanation_is_used_by_the_tree():
    """R1 on the real tree, both directions."""
    concrete, _globs = bl.declared()
    mapped = bl.ledger_files(bl.ledger())
    assert set(concrete) == set(mapped)


def test_no_row_is_stale_on_the_live_tree(live):
    """R3 on the real tree: every declared file must suppress something."""
    concrete, _globs = bl.declared()
    stale = [rel for rel in concrete if live.get(rel, 0) == 0]
    assert stale == [], f"declared but suppressing nothing: {stale}"


def test_census_is_quiet_on_the_live_tree():
    """Nothing blind is both undeclared and unannotated, so census proposes nothing."""
    concrete, _ = bl.declared()
    undeclared = {rel: n for rel, n in bl.live_sites().items() if rel not in concrete}
    assert undeclared == {}


# ---------------------------------------------------------------------------
# The premise: --isolated is the only way to see what the declaration hides
# ---------------------------------------------------------------------------


def test_isolated_reveals_exactly_what_per_file_ignores_hides(live):
    """The gate's whole guarantee rests on this, so it is asserted, not assumed."""
    concrete, _ = bl.declared()

    # With configuration applied, ruff is satisfied: that is why a stale row is
    # invisible, and why the backlog cannot be measured by a normal run.
    configured = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--select", RULE,
         "--output-format", "concise", bl.SRC],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert configured.stdout.strip() in {"", "All checks passed!"} or RULE not in configured.stdout, (
        "a declared file now fails ruff even with per-file-ignores applied — the "
        "declaration and the config have diverged"
    )

    # And the isolated measurement is non-empty, or the staleness check would
    # declare every row stale.
    assert live, "isolated measurement found nothing; the staleness check cannot be trusted"
    assert set(live) <= set(concrete), (
        "isolated measurement found violations outside the declaration: "
        f"{sorted(set(live) - set(concrete))}"
    )


# ---------------------------------------------------------------------------
# The gate can fail
# ---------------------------------------------------------------------------


def test_a_row_with_no_ledger_entry_is_reported():
    """The silent re-addition: a suppression added to make ruff green."""
    result = bl.Result()
    bl.check_agreement(result, {"src/msb_v3/api/app.py": [RULE]}, {})
    assert len(result.findings) == 1
    assert "no written reason" in result.findings[0]
    assert "src/msb_v3/api/app.py" in result.findings[0]


def test_a_ledger_entry_with_no_row_is_reported():
    """The other direction: an argument left behind after the row was deleted."""
    result = bl.Result()
    bl.check_agreement(result, {}, {"src/msb_v3/api/app.py": "a reason"})
    assert len(result.findings) == 1
    assert "no per-file-ignores row" in result.findings[0]


def test_a_group_with_no_reason_is_reported():
    result = bl.Result()
    bl.check_reasons(result, {"groups": [{"id": "x", "reason": "   ", "files": ["a.py"]}]}, {})
    assert any("has no reason" in f for f in result.findings)


def test_an_empty_group_is_reported():
    result = bl.Result()
    bl.check_reasons(result, {"groups": [{"id": "x", "reason": "ok", "files": []}]}, {})
    assert any("dead weight" in f for f in result.findings)


def test_an_unargued_class_exemption_is_reported():
    """A glob suppresses a whole class, so it needs the argument."""
    result = bl.Result()
    bl.check_reasons(result, {"groups": [{"id": "x", "reason": "ok", "files": ["a.py"]}]},
                     {"tests/**": [RULE]})
    assert any("does not argue for" in f for f in result.findings)


def test_an_argument_for_a_class_that_is_not_declared_is_reported():
    result = bl.Result()
    bl.check_reasons(
        result,
        {"groups": [{"id": "x", "reason": "ok", "files": ["a.py"]}],
         "class_exemptions": {"tests/**": "argued"}},
        {},
    )
    assert any("about nothing" in f for f in result.findings)


def test_a_stale_row_is_reported():
    """The check that makes burn-down legible: a row suppressing nothing must go."""
    result = bl.Result()
    bl.check_stale(result, {"src/msb_v3/api/app.py": [RULE]}, {})
    assert len(result.findings) == 1
    assert "suppresses nothing" in result.findings[0]
    assert result.stats["stale rows"] == 1


def test_growth_past_the_ceiling_is_reported():
    """A new file or new sites in a declared file must be a deliberate ledger edit."""
    result = bl.Result()
    concrete = {"src/msb_v3/api/app.py": [RULE]}
    bl.check_ceilings(result, {"ceilings": _ceiling(0, 0)}, concrete, {"src/msb_v3/api/app.py": 3})
    assert any("grew past its ceiling" in f for f in result.findings)


def test_a_missing_ceiling_is_reported():
    result = bl.Result()
    bl.check_ceilings(result, {}, {"src/msb_v3/api/app.py": [RULE]}, {})
    assert sum("no integer ceiling" in f for f in result.findings) == 2


def test_a_slack_ceiling_is_noted_rather_than_failed():
    """Slack is reported so it stays visible, but it is not a failure."""
    result = bl.Result()
    bl.check_ceilings(
        result, {"ceilings": _ceiling(1, 999)}, {"src/msb_v3/api/app.py": [RULE]},
        {"src/msb_v3/api/app.py": 1},
    )
    assert result.findings == []
    assert any("slack" in note for note in result.notes)


def test_an_undeclared_file_with_violations_is_reported():
    result = bl.Result()
    bl.check_coverage(result, {}, {"src/msb_v3/api/new.py": 2})
    assert len(result.findings) == 1
    assert "declared neither" in result.findings[0]


def test_a_disarmed_rule_is_reported(monkeypatch):
    """If BLE001 leaves `select`, every row below it is decoration."""
    monkeypatch.setattr(bl, "ruff_config", lambda: {"select": ["E4", "F"]})
    result = bl.Result()
    bl.check_armed(result)
    assert len(result.findings) == 1
    assert "nothing enforces" in result.findings[0]
    assert result.stats["rule armed"] == 0


def test_armed_rule_passes_quietly(monkeypatch):
    monkeypatch.setattr(bl, "ruff_config", lambda: {"select": [RULE]})
    result = bl.Result()
    bl.check_armed(result)
    assert result.findings == []
    assert result.stats["rule armed"] == 1


# ---------------------------------------------------------------------------
# Input failures are told apart from tree failures
# ---------------------------------------------------------------------------


def test_a_broken_ledger_is_a_gate_error_not_a_finding(tmp_path, monkeypatch):
    """A malformed ledger is the operator's problem, and must not read as drift."""
    bad = tmp_path / "ble001-backlog.json"
    bad.write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(bl, "LEDGER", bad)
    with pytest.raises(bl.GateError, match="cannot read"):
        bl.ledger()
