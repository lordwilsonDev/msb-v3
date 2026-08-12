"""CLI tests: python -m msb_v3.flywheel config.

Pins the loop's config view — human-readable lines and --json — against
the shared guard_config() builder, and proves the two operator consoles
(governance + flywheel) print identically by construction.
"""

from __future__ import annotations

import json

from msb_v3.core.guard_config import guard_config
from msb_v3.flywheel.cli import main


def test_config_exits_zero_and_prints_blocks(capsys) -> None:
    assert main(["config"]) == 0
    out = capsys.readouterr().out
    # the loop's view: flywheel mechanics first
    assert "[flywheel] stages (9):" in out
    assert "[flywheel] iterations per stage: 1" in out
    assert "[flywheel] research-call spenders: charge, scan_papers" in out
    # and the brakes that gate it
    assert "[governance] budget caps per rolling window:" in out
    assert "[governance] approval stages:" in out
    assert "[rate] chat:" in out


def test_config_json_matches_shared_builder(capsys) -> None:
    """--json emits the verbatim guard_config() blocks — the exact shape
    /system/config serves, so a CLI diff against the endpoint is a true
    parity check."""
    assert main(["config", "--json"]) == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed == guard_config()
    assert set(parsed) == {"rate_limits", "governance", "approvals", "flywheel"}


def test_config_identical_to_governance_console(capsys) -> None:
    """Both operator consoles render the same blocks — make governance-config
    and make flywheel-config print byte-identical output by construction."""
    from msb_v3.governance.cli import main as gov_main

    assert main(["config"]) == 0
    fly = capsys.readouterr().out
    assert gov_main(["config"]) == 0
    gov = capsys.readouterr().out
    assert fly == gov
