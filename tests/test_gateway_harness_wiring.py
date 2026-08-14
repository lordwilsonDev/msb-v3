"""ChatHarness × Capability Gateway wiring tests.

Pins three behaviors after the gateway landed inside
`ChatHarness.execute`:

1. **Default path unchanged** — a call with no gate fields routes as it
   always did (local backend via the client factory) and telemetry now
   carries `decision_id` + `gateway_reason` so the dispatch is
   replayable from the audit chain.
2. **Opt-in denial is loud** — when the context sets
   `requires_authorization` and no matching grant exists, the harness
   returns `ok=False`, `event="chat:denied"`, and does NOT touch the
   model client (the `[fallback]` path is bypassed — a denied call is
   an event, not a degraded outcome).
3. **Denials are still auditable** — the denial lands in the audit
   chain via the gateway's own append.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from msb_v3.harnesses.base import ChatHarness  # noqa: E402


class _StubClient:
    """Satisfies ChatHarness.execute_tool_loop without a model."""

    def __init__(self):
        self.calls = 0

    def execute_tool_loop(self, query, *, system=None, tools=None, max_steps=4, max_tokens=2048):
        self.calls += 1
        from msb_v3.local_ai.ollama import LocalAIResponse

        return LocalAIResponse(
            text="ok", model="stub", latency_s=0.01,
            prompt_tokens=3, completion_tokens=2,
        )


@pytest.fixture
def audit_db(tmp_path):
    return tmp_path / "audit_chain.db"


@pytest.fixture(autouse=True)
def _isolated_audit(monkeypatch, audit_db):
    import msb_v3.uac.audit_chain as ac_mod
    monkeypatch.setattr(ac_mod, "_AUDIT_DB", audit_db)


def test_default_path_routes_and_records_decision_id():
    """No gate fields => identical routing, but decision_id rides telemetry."""
    stub = _StubClient()
    harness = ChatHarness(client=stub)
    result = harness.execute("hello", session="s1")

    assert result.ok
    assert result.event == "chat:completed"
    assert stub.calls == 1  # the model client was reached
    assert "decision_id" in result.telemetry
    assert "gateway_reason" in result.telemetry
    # Default: no capabilities/authorization demanded => routed locally.
    assert "fits_in_local_budget" in result.telemetry["gateway_reason"]


def test_opt_in_denial_is_loud_and_never_touches_model():
    """requires_authorization w/o grant => ok=False, event=chat:denied,
    and the stub client is never called (no fallback, no model contact)."""
    stub = _StubClient()
    harness = ChatHarness(client=stub)
    result = harness.execute(
        "intervene",
        context={
            "requires_authorization": True,
            "slug": "rj-2026-08-13",
        },
        session="s2",
    )

    assert result.ok is False
    assert result.event == "chat:denied"
    assert "requires_authorization_not_granted" in result.error
    assert stub.calls == 0, "denied call must not contact the model"
    assert result.telemetry["decision_id"]


def test_denial_lands_in_audit_chain(audit_db):
    """The denial is a gateway event — replayable from the audit chain."""
    stub = _StubClient()
    harness = ChatHarness(client=stub)
    result = harness.execute(
        "intervene",
        context={
            "requires_authorization": True,
            "slug": "rj-2026-08-13",
        },
        session="s3",
    )
    with sqlite3.connect(audit_db) as conn:
        rows = conn.execute(
            "SELECT event_type FROM audit_records WHERE record_hash = ?",
            (result.telemetry["decision_id"],),
        ).fetchall()
    assert rows and rows[0][0] == "call.denied"


def test_matching_grant_allows_opt_in_call():
    """With the slug-keyed grant present, the same call proceeds."""
    stub = _StubClient()
    harness = ChatHarness(client=stub)
    result = harness.execute(
        "intervene",
        context={
            "requires_authorization": True,
            "slug": "rj-2026-08-13",
            "granted_authorizations": ["chat.llm:rj-2026-08-13"],
        },
        session="s4",
    )
    assert result.ok
    assert stub.calls == 1
    assert result.event == "chat:completed"
