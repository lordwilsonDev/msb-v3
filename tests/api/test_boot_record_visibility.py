"""The first record in the ledger must not vanish silently.

`api/app.py`'s lifespan writes a `boot.started` event into the hash-chained audit
log. That write is best-effort on purpose — a chain that refuses the append must
never block startup — and it was a bare `except Exception: pass`, which made a
broken chain indistinguishable from a working one.

These tests pin both halves of the fix: startup survives a refused append, and
the refusal is visible.
"""

from __future__ import annotations

import asyncio
import logging

import msb_ledger.chain_anchor as chain_anchor
from msb_v3.api.app import create_app, lifespan

# Entering the real lifespan, on the real app, is the point: the write under test
# happens during startup, not in a function the test could call in isolation.
APP = create_app()


async def _boot_once() -> None:
    async with lifespan(APP):
        pass


def test_a_refused_boot_record_is_logged_and_does_not_block_startup(monkeypatch, caplog):
    def refuse() -> None:
        raise RuntimeError("chain refused the append")

    monkeypatch.setattr(chain_anchor, "anchored_chain_from_env", refuse)

    with caplog.at_level(logging.WARNING, logger="msb_v3.api.app"):
        asyncio.run(_boot_once())

    assert "boot record not written to the audit chain" in caplog.text
    assert "RuntimeError" in caplog.text, "the log must name the failure, not just report one"


def test_a_working_chain_is_not_a_warning(monkeypatch, caplog):
    appended: list[tuple] = []

    class _Chain:
        def append(self, *args) -> None:
            appended.append(args)

    monkeypatch.setattr(chain_anchor, "anchored_chain_from_env", lambda: _Chain())

    with caplog.at_level(logging.WARNING, logger="msb_v3.api.app"):
        asyncio.run(_boot_once())

    assert appended, "the boot record is still written when the chain accepts it"
    assert "boot record not written" not in caplog.text
