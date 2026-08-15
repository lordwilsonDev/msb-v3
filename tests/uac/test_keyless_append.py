"""Fail-closed guard: no keyless appends to an anchored audit chain.

Two signals refuse a bare AuditChain() append:

1. Chain-global: the target chain carries a signed anchor file
   (chain_anchor.json next to the DB) — the anchor is a property of the
   CHAIN, so even a process whose own env has no key is refused (this is
   the hole where keyless background loops — flywheel, agent pipeline —
   appended to the shared production chain and only healed when the daily
   verify job re-signed).
2. Process-local: MSB_CHAIN_ANCHOR_KEY is configured and this is the
   default production chain.

The sanctioned path is the AnchoredAuditChain wrapper (re-anchors every
append). Separate chains without an anchor file (node perimeter, tests,
custom DBs) and the explicit escape hatches (allow_keyless=True,
MSB_ALLOW_KEYLESS_APPENDS=1) are unaffected.

``_AUDIT_DB`` is monkeypatched to a tmp path so "the default chain" in
these tests is a scratch file, never the live production DB.
"""
from __future__ import annotations

import os

import pytest

from msb_v3.uac import audit_chain as ac
from msb_v3.uac.audit_chain import AuditChain, AuditChainKeylessAppendError
from msb_v3.uac.chain_anchor import AnchoredAuditChain, ChainAnchor

KEY = "MSB_CHAIN_ANCHOR_KEY"
ALLOW = "MSB_ALLOW_KEYLESS_APPENDS"


@pytest.fixture
def default_db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the module-level default chain at a scratch file."""
    monkeypatch.setattr(ac, "_AUDIT_DB", tmp_path / "audit.db")


def _seed() -> bytes:
    from msb_v3.uac.chain_anchor import generate_seed

    return generate_seed()


def test_bare_default_chain_append_refused_when_key_configured(
    default_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(KEY, _seed().hex())
    chain = AuditChain()
    with pytest.raises(AuditChainKeylessAppendError, match="keyless append refused"):
        chain.append("test", "event", {"i": 1})
    # nothing was written — the refusal happens before any sqlite write
    assert chain.verify_chain()["valid"] is True
    assert chain.verify_chain()["record_count"] == 0


def test_anchored_wrapper_is_the_sanctioned_append_path(
    default_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed = _seed()
    monkeypatch.setenv(KEY, seed.hex())
    wrapped = AnchoredAuditChain(AuditChain(), ChainAnchor(seed=seed))
    wrapped.append("test", "event", {"i": 1})
    assert wrapped.verify_chain()["valid"] is True
    assert wrapped.verify_chain()["record_count"] == 1
    assert wrapped.verify_anchored()["valid"] is True
    assert wrapped.verify_anchored()["stale"] is False


def test_custom_db_path_is_unaffected_by_the_guard(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Separate chains (e.g. the node perimeter's own audit DB) keep working."""
    monkeypatch.setenv(KEY, _seed().hex())
    chain = AuditChain(db_path=str(tmp_path / "node" / "audit.db"))
    chain.append("node", "session.opened", {"device_id": "d"})
    assert chain.verify_chain()["valid"] is True
    assert chain.verify_chain()["record_count"] == 1


def test_no_key_configured_allows_bare_append(
    default_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(KEY, raising=False)
    monkeypatch.delenv(ALLOW, raising=False)
    chain = AuditChain()
    chain.append("test", "event", {})
    assert chain.verify_chain()["record_count"] == 1


def test_allow_keyless_constructor_flag_is_an_explicit_escape_hatch(
    default_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(KEY, _seed().hex())
    chain = AuditChain(allow_keyless=True)
    chain.append("test", "event", {})
    assert chain.verify_chain()["record_count"] == 1


def test_msb_allow_keyless_appends_env_is_an_explicit_escape_hatch(
    default_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(KEY, _seed().hex())
    monkeypatch.setenv(ALLOW, "1")
    chain = AuditChain()
    chain.append("test", "event", {})
    assert chain.verify_chain()["record_count"] == 1


def test_bare_append_refused_when_chain_has_anchor_file_even_without_env_key(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Chain-global invariant: a chain that carries chain_anchor.json is
    refused bare appends even when the process env has NO key — this is the
    exact hole the keyless flywheel/agent loops used (their own process
    lacked the key, but the shared production chain was anchored)."""
    monkeypatch.delenv(KEY, raising=False)
    monkeypatch.delenv(ALLOW, raising=False)
    (tmp_path / ac._ANCHOR_FILENAME).write_text('{"fake": "anchor"}')
    chain = AuditChain(db_path=str(tmp_path / "audit.db"))
    with pytest.raises(AuditChainKeylessAppendError, match="signed anchor file"):
        chain.append("test", "event", {"i": 1})
    assert chain.verify_chain()["record_count"] == 0


def test_allow_keyless_flag_bypasses_chain_global_guard(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(KEY, raising=False)
    monkeypatch.delenv(ALLOW, raising=False)
    (tmp_path / ac._ANCHOR_FILENAME).write_text('{"fake": "anchor"}')
    chain = AuditChain(db_path=str(tmp_path / "audit.db"), allow_keyless=True)
    chain.append("test", "event", {})
    assert chain.verify_chain()["record_count"] == 1


def test_guard_key_configuration_matches_chain_anchor_factory(
    default_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard must agree with anchored_chain_from_env() about whether a
    key is configured (env var OR default key file)."""
    # env unset, default key file absent -> both say "no key"
    monkeypatch.delenv(KEY, raising=False)
    assert ac._chain_key_configured() is False
    from msb_v3.uac.chain_anchor import anchored_chain_from_env

    assert type(anchored_chain_from_env()).__name__ == "AuditChain"
    # env set -> both say "key configured"
    monkeypatch.setenv(KEY, _seed().hex())
    assert ac._chain_key_configured() is True
    assert type(anchored_chain_from_env()).__name__ == "AnchoredAuditChain"
