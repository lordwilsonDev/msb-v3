"""CLI tests: python -m msb_v3.governance config.

Pins the operator-visible config surface — the human-readable lines and
the --json output — against the shared guard_config() builder, which is
the same source /system/config serves from. If the CLI and the endpoint
drift, guard_config() is the single point of truth to fix.
"""

from __future__ import annotations

import json

from msb_v3.core.guard_config import guard_config
from msb_v3.governance.cli import main


def test_config_exits_zero_and_prints_blocks(capsys) -> None:
    assert main(["config"]) == 0
    out = capsys.readouterr().out
    # budget caps, governor thresholds, approval policy, flywheel mechanics,
    # and the /v1 rate guards all show up as human-readable lines
    assert "[governance] budget caps per rolling window:" in out
    assert "tokens: " in out and "iterations: " in out
    assert "[governance] governor thresholds:" in out
    assert "[governance] approval kinds: build, combine" in out
    assert "[governance] approval stages:" in out
    assert "[flywheel] stages (9):" in out
    assert "[flywheel] iterations per stage: 1" in out
    assert "[flywheel] research-call spenders: charge, scan_papers" in out
    assert "[rate] chat:" in out and "embed:" in out


def test_config_json_is_identical_to_shared_builder(capsys) -> None:
    """--json must emit the verbatim guard_config() blocks — the exact
    shape /system/config serves, so a CLI diff against the endpoint is a
    true parity check."""
    assert main(["config", "--json"]) == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed == guard_config()
    assert set(parsed) == {"rate_limits", "governance", "approvals", "flywheel"}
