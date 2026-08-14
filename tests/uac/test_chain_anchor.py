from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from msb_v3.uac.audit_chain import AuditChain
from msb_v3.uac.chain_anchor import (
    KEY_ENV,
    AnchoredAuditChain,
    ChainAnchor,
    anchored_chain_from_env,
    generate_seed,
)


def make_chain(db_path: Path, n: int) -> AuditChain:
    chain = AuditChain(str(db_path))
    for i in range(n):
        chain.append("test", "normal", {"i": i})
    return chain


def test_anchor_verify_roundtrip(tmp_path: Path) -> None:
    chain = make_chain(tmp_path / "audit.db", 5)
    anchor = ChainAnchor(seed=generate_seed())
    anchor.anchor(chain)

    result = anchor.verify(chain)
    assert result["valid"] is True
    assert result["record_count"] == 5


def test_t7_whole_db_replacement_is_detected(tmp_path: Path) -> None:
    """The exact T7 attack: replace the whole DB with an older internally-valid
    chain. The hash chain stays green; the external anchor catches it."""
    db = tmp_path / "audit.db"
    chain = make_chain(db, 5)
    anchor = ChainAnchor(seed=generate_seed())
    anchor.anchor(chain)

    # attacker builds a fresh internally-valid chain and swaps the DB file
    fresh = make_chain(tmp_path / "fresh.db", 3)
    os.replace(tmp_path / "fresh.db", db)

    internal = chain.verify_chain()
    anchored = anchor.verify(chain)

    assert internal["valid"] is True  # hash chain alone cannot see the swap
    assert anchored["valid"] is False
    assert "whole-DB replacement" in anchored["reason"]
    assert anchored["anchored_seq"] == 5
    assert anchored["live_seq"] == 3


def test_anchor_file_tamper_is_detected(tmp_path: Path) -> None:
    chain = make_chain(tmp_path / "audit.db", 3)
    anchor = ChainAnchor(seed=generate_seed())
    anchor.anchor(chain)

    anchor_file = tmp_path / "chain_anchor.json"
    data = json.loads(anchor_file.read_text())
    data["snapshot"]["tip_hash"] = "f" * 64
    anchor_file.write_text(json.dumps(data))

    result = anchor.verify(chain)
    assert result["valid"] is False
    assert "signature invalid" in result["reason"]


def test_wrong_key_is_detected(tmp_path: Path) -> None:
    chain = make_chain(tmp_path / "audit.db", 3)
    signer = ChainAnchor(seed=generate_seed())
    signer.anchor(chain)

    other = ChainAnchor(seed=generate_seed())  # different key, same anchor file
    result = other.verify(chain)
    assert result["valid"] is False
    assert "public key does not match" in result["reason"]


def test_stale_anchor_after_legitimate_append(tmp_path: Path) -> None:
    chain = make_chain(tmp_path / "audit.db", 3)
    anchor = ChainAnchor(seed=generate_seed())
    anchor.anchor(chain)

    chain.append("test", "normal", {"i": 99})  # append AFTER anchoring
    result = anchor.verify(chain)
    assert result["valid"] is False  # stale anchor is detectable by design

    anchor.anchor(chain)  # re-anchor after the legitimate append
    assert anchor.verify(chain)["valid"] is True


def test_wrapper_reanchors_after_every_append(tmp_path: Path) -> None:
    chain = make_chain(tmp_path / "audit.db", 0)
    anchored = AnchoredAuditChain(chain, ChainAnchor(seed=generate_seed()))
    for i in range(3):
        anchored.append("test", "normal", {"i": i})

    assert anchored.verify_anchored()["valid"] is True
    assert anchored.verify_chain()["valid"] is True
    assert len(anchored.get_chain()) == 3

    # T7 swap against the anchored wrapper
    make_chain(tmp_path / "replacement.db", 2)
    os.replace(tmp_path / "replacement.db", tmp_path / "audit.db")
    assert anchored.verify_anchored()["valid"] is False


def test_notarized_export_verifies_with_public_key_only(tmp_path: Path) -> None:
    """Verify-only machines (no signing key) can validate against the exported
    signed anchor — the out-of-band notarization story."""
    chain = make_chain(tmp_path / "audit.db", 4)
    signer = ChainAnchor(seed=generate_seed())
    signer.anchor(chain)
    export = tmp_path / "notary" / "anchor.json"
    signer.notarize(chain, export, append=False)

    verifier = ChainAnchor(public_key=signer._pub, anchor_path=export)
    assert verifier.verify(chain)["valid"] is True

    # and the notarized copy detects a DB swap too
    fresh = make_chain(tmp_path / "swap.db", 1)
    os.replace(tmp_path / "swap.db", tmp_path / "audit.db")
    assert verifier.verify(chain)["valid"] is False


def test_factory_plain_without_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import msb_v3.uac.chain_anchor as mod

    monkeypatch.delenv(KEY_ENV, raising=False)
    monkeypatch.setattr(mod, "_default_key_path", lambda: tmp_path / "none.key")
    assert isinstance(anchored_chain_from_env(), AuditChain)


def test_factory_anchored_with_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import msb_v3.uac.chain_anchor as mod

    monkeypatch.setenv(KEY_ENV, generate_seed().hex())
    monkeypatch.setattr(mod, "_default_key_path", lambda: tmp_path / "none.key")
    result = anchored_chain_from_env()
    assert isinstance(result, AnchoredAuditChain)
    assert result.verify_anchored()["valid"] is True
