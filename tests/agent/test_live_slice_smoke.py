"""Live end-to-end smoke test for the agent slice (Phase 1 hardening #9).

This is the un-mocked model hop the audit asked for: it runs the real
intent -> plan -> gated-execute -> verify path against the live Ollama model
and the live Qdrant tenant collection — no fake clients anywhere.

Opt-in by design: it requires a running server stack (Ollama + Qdrant), so
it skips unless MSB_LIVE_TESTS=1. When enabled, its verdict is honest: if
the model or retrieval is down, `result.ok` is False and the test fails
loudly — a live smoke test that cannot fail is not a smoke test.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("MSB_LIVE_TESTS") != "1",
    reason="opt-in live smoke test — set MSB_LIVE_TESTS=1 against a running stack",
)


def test_live_agent_slice_end_to_end(tmp_path: Path) -> None:
    from msb_v3.agent.handle import handle

    result = asyncio.run(
        handle(
            "Write a one-line note about the sovereign stack and save it to the vault.",
            tenant="wilson-vault",
            approve=True,
            output_dir=str(tmp_path),
            session="live-smoke",
        )
    )
    assert result.ok, f"live slice failed: {result.error}"
    assert result.verdict == "PASS", f"verification failed: {result.verdict}"
    assert result.deterministic_hash, "deterministic hash missing"

    # The grounded file_written verifier already checked this, but pin the
    # artifact on disk from the test side too (evidence > claim).
    notes = list(tmp_path.glob("*.md"))
    assert notes, "no brief file written under output_dir"
    assert notes[0].read_text().startswith("# "), "brief file lacks the H1 heading"
