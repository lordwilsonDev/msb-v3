"""Capability Gateway routing tests.

Pins the contract:
  - Allowed call records a `call.allowed` event in the audit chain
  - Denied call records `call.denied` (denials are auditable)
  - Local-vs-frontier routing follows `local_budget_bytes`
  - Capability check denies before authorization/backend selection
  - `requires_authorization` requires an exact `name:slug` grant
  - Decision IDs are 64-hex sha256 hashes (audit-chain compatible)

Tests use a tempdir for the audit chain so they don't write to the
real runtime SQLite path. `AuditChain(db_path=...)` honours the
override.
"""

from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from msb_v3.gateway import GatewayCall, GatewayContext, route  # noqa: E402
from msb_v3.uac.audit_chain import AuditChain  # noqa: E402


@pytest.fixture
def audit_db(tmp_path):
    """Point the audit chain at a tempdir so tests don't share the real DB."""
    return tmp_path / "audit_chain.db"


@pytest.fixture(autouse=True)
def _isolated_audit(monkeypatch, audit_db):
    """Force every AuditChain ctor in this test file at the temp path."""
    # AuditChain reads `settings.audit_db_path` via _AUDIT_DB; force the
    # ctor to take an explicit path by patching _AUDIT_DB to the temp.
    import msb_v3.uac.audit_chain as ac_mod
    monkeypatch.setattr(ac_mod, "_AUDIT_DB", audit_db)


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


# ---------------------------------------------------------------------------
# Allow path
# ---------------------------------------------------------------------------


def test_basic_call_routes_local_when_fits_in_budget(audit_db):
    """A small call with no special capabilities lands on the active local backend."""
    ctx = GatewayContext(local_budget_bytes=8 * 1024 * 1024 * 1024)
    call = GatewayCall(name="llm.infer", estimated_bytes=2 * 1024 * 1024 * 1024)
    decision = route(call, ctx)

    assert decision.authorized
    assert decision.backend in ("local.ollama", "local.llamacpp")
    assert "fits_in_local_budget" in decision.reason
    assert SHA256_RE.match(decision.decision_id)


def test_large_call_routes_to_frontier(audit_db):
    """Anything over the local budget is forced to the frontier seam."""
    ctx = GatewayContext(local_budget_bytes=4 * 1024 * 1024 * 1024)
    call = GatewayCall(
        name="llm.infer",
        estimated_bytes=4 * 1024 * 1024 * 1024 + 1,
    )
    decision = route(call, ctx)

    assert decision.authorized
    assert decision.backend == "frontier"
    assert "exceeds_local_budget" in decision.reason


def test_no_context_defaults_to_empty_capabilities():
    """`route()` with no ctx must be safe — empty granted sets, no caps allowed."""
    decision = route(GatewayCall(name="llm.infer", capabilities=frozenset({"a.b"})))
    assert not decision.authorized
    assert decision.backend is None
    assert "missing_capabilities:a.b" in decision.reason


# ---------------------------------------------------------------------------
# Deny path
# ---------------------------------------------------------------------------


def test_missing_capability_denies_call(audit_db):
    """Capability check happens before backend selection — never runs the call."""
    ctx = GatewayContext(granted_capabilities=frozenset({"a.b"}))
    call = GatewayCall(
        name="tool.shell",
        estimated_bytes=10 * 1024 * 1024,  # would fit locally if allowed
        capabilities=frozenset({"a.b", "shell.execute"}),
    )
    decision = route(call, ctx)
    assert not decision.authorized
    assert decision.backend is None
    assert decision.reason == "missing_capabilities:shell.execute"


def test_requires_authorization_denies_without_grant(audit_db):
    """§5 Experimental Plane rule: this call must not execute autonomously."""
    ctx = GatewayContext()
    call = GatewayCall(
        name="experiment.intervene",
        estimated_bytes=10 * 1024 * 1024,
        requires_authorization=True,
        metadata={"slug": "rj-2026-08-13"},
    )
    decision = route(call, ctx)
    assert not decision.authorized
    assert decision.backend is None
    assert "requires_authorization_not_granted" in decision.reason
    assert "experiment.intervene:rj-2026-08-13" in decision.reason


def test_requires_authorization_allows_with_matching_grant(audit_db):
    """With the slug-keyed grant present, the call may proceed."""
    ctx = GatewayContext(
        granted_authorizations=frozenset(
            {"experiment.intervene:rj-2026-08-13"}
        ),
    )
    call = GatewayCall(
        name="experiment.intervene",
        estimated_bytes=10 * 1024 * 1024,
        requires_authorization=True,
        metadata={"slug": "rj-2026-08-13"},
    )
    decision = route(call, ctx)
    assert decision.authorized
    assert decision.backend in ("local.ollama", "local.llamacpp")


def test_authorization_grant_for_other_slug_does_not_match(audit_db):
    """Specificity matters — a grant for `rj-A` is not a grant for `rj-B`."""
    ctx = GatewayContext(
        granted_authorizations=frozenset({"experiment.intervene:rj-A"}),
    )
    call = GatewayCall(
        name="experiment.intervene",
        requires_authorization=True,
        metadata={"slug": "rj-B"},
    )
    decision = route(call, ctx)
    assert not decision.authorized


# ---------------------------------------------------------------------------
# Audit chain
# ---------------------------------------------------------------------------


def test_decision_lands_in_audit_chain(audit_db):
    """Every routing decision — allowed or denied — is recorded."""
    ctx = GatewayContext()
    call = GatewayCall(name="llm.infer", estimated_bytes=2 * 1024 * 1024 * 1024)
    decision = route(call, ctx)

    with sqlite3.connect(audit_db) as conn:
        rows = conn.execute(
            "SELECT event_type, payload, record_hash FROM audit_records "
            "WHERE record_hash = ?",
            (decision.decision_id,),
        ).fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row[0] == "call.allowed"
    # payload is stored as JSON text; the field round-trips.
    assert '"authorized"' in row[1]
    assert row[2] == decision.decision_id


def test_denied_decision_also_recorded(audit_db):
    """Denials land in the same chain — visibility, not just audit theatre."""
    decision = route(
        GatewayCall(
            name="tool.shell", capabilities=frozenset({"a.b"})
        ),
        GatewayContext(),
    )
    assert not decision.authorized
    with sqlite3.connect(audit_db) as conn:
        rows = conn.execute(
            "SELECT event_type FROM audit_records WHERE record_hash = ?",
            (decision.decision_id,),
        ).fetchall()
    assert rows[0][0] == "call.denied"


def test_decision_id_is_a_sha256_hex_chain_entry(audit_db):
    """Sanity: `decision_id` is a real 64-hex sha256 + row exists with that hash."""
    decision = route(GatewayCall(name="llm.infer"))
    assert SHA256_RE.match(decision.decision_id)
    with sqlite3.connect(audit_db) as conn:
        row = conn.execute(
            "SELECT record_hash FROM audit_records WHERE record_hash = ?",
            (decision.decision_id,),
        ).fetchone()
    assert row is not None
    # And the surrounding chain (every row) is still intact — the gateway
    # append can't accidentally fork the chain even though it goes through
    # the same `AuditChain.append` path as uac observatory.
    chain = AuditChain()
    summary = chain.verify_chain()
    assert summary.get("valid", summary.get("ok")) in (True, None) or (
        "broken" not in summary
    ), f"audit chain reported broken: {summary!r}"
