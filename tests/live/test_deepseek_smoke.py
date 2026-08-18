"""Live DeepSeek provider smoke — the provable-harness close (Phase 1).

Proves the DeepSeek native API end-to-end against the live model:

    DeepSeekAgentProvider -> handle() -> MoIE gate -> intent -> plan ->
    gated tools -> verify -> evidence spine -> ledger -> receipt

Opt-in: requires ``MSB_LIVE_TESTS=1`` AND a DeepSeek key
(``DEEPSEEK_API_KEY``, falling back to ``OPENAI_API_KEY``). When enabled it
runs a real task through DeepSeek and asserts the evidence receipt lands in
the canonical audit stream with a non-empty audit hash — the "receipt in the
Evidence Stream with a verified audit hash" done-definition. When the key is
absent it skips (never fails): the seam is closed, not broken.

A deeper check (proving ``audit_hash`` is *in* the hash chain) is the
ledger's job, not this smoke's: ``python -m msb_ledger.chain_anchor
--verify-receipt ...`` against the live chain.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("MSB_LIVE_TESTS") != "1",
    reason="opt-in live smoke test — set MSB_LIVE_TESTS=1 with a DeepSeek key",
)


def test_deepseek_provider_live_receipt(tmp_path: Path) -> None:
    from msb_v3.agent.providers import DeepSeekAgentProvider
    from msb_v3.core.config import settings

    if not settings.deepseek_api_key:
        pytest.skip("DEEPSEEK_API_KEY not set — DeepSeek seam closed")

    goal = (
        "Search the vault for recent decisions about the sovereign stack and "
        "summarize them. Do not write any files."
    )
    result = asyncio.run(
        DeepSeekAgentProvider().execute(
            goal,
            context={"approve": True, "output_dir": str(tmp_path)},
        )
    )
    assert result.ok, f"deepseek live run failed: {result.error}"
    run_id = result.artifacts["run_id"]
    assert run_id, "run id missing"

    stream = Path(settings.audit_log_path)
    assert stream.exists(), f"audit stream missing: {stream}"
    receipts = [json.loads(ln) for ln in stream.read_text().splitlines() if ln.strip()]
    mine = [r for r in receipts if r.get("request_id") == run_id]
    assert mine, f"no receipt for run {run_id} in {stream}"
    receipt = mine[-1]

    assert receipt["execution_result"]["verdict"] == "PASS"
    assert receipt["verification_result"] == result.artifacts["deterministic_hash"]
    assert receipt["audit_hash"], "receipt has no audit hash"
