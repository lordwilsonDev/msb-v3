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
    make_chain(tmp_path / "fresh.db", 3)
    os.replace(tmp_path / "fresh.db", db)

    internal = chain.verify_chain()
    anchored = anchor.verify(chain)

    assert internal["valid"] is True  # hash chain alone cannot see the swap
    assert anchored["valid"] is False
    assert "whole-DB replacement" in anchored["reason"]
    assert anchored.get("stale", False) is False  # replacement, not staleness
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

    # a legacy keyless process appends via the explicit escape hatch — the
    # exact scenario the chain-global guard refuses for real keyless processes
    AuditChain(str(tmp_path / "audit.db"), allow_keyless=True).append("test", "normal", {"i": 99})
    result = anchor.verify(chain)
    assert result["valid"] is False  # stale anchor is detectable by design

    anchor.anchor(chain)  # re-anchor after the legitimate append
    assert anchor.verify(chain)["valid"] is True


def test_verify_reports_staleness_but_stays_valid(tmp_path: Path) -> None:
    """A valid-for-its-tip anchor that is older than newer chain records must
    be flagged stale (re-anchoring stopped) while remaining valid."""
    chain = make_chain(tmp_path / "audit.db", 3)
    anchor = ChainAnchor(seed=generate_seed())
    anchored = AnchoredAuditChain(chain, anchor)  # re-anchors on every append
    for i in range(3, 6):
        anchored.append("test", "normal", {"i": i})
    healthy = anchor.verify(chain)
    assert healthy["valid"] is True
    assert healthy["stale"] is False

    # simulate re-anchoring stopping: a legacy keyless process appends via the
    # explicit escape hatch (bypassing the wrapper) so a newer record exists
    # than the signed anchor covers
    AuditChain(str(tmp_path / "audit.db"), allow_keyless=True).append("test", "normal", {"i": 99})
    result = anchor.verify(chain)
    assert result["valid"] is False  # the chain is no longer covered
    assert result["stale"] is True
    assert "STALE" in result["reason"]
    assert result["stale_seconds"] > 0


def _stale_chain(db_path: Path, seed: bytes) -> None:
    """Build a chain whose anchor (signed with ``seed``) covers an older tip
    — benign staleness from records appended after the last anchor by a
    legacy keyless process (simulated via the explicit escape hatch)."""
    chain = make_chain(db_path, 3)
    ChainAnchor(seed=seed).anchor(chain)
    AuditChain(str(db_path), allow_keyless=True).append("test", "normal", {"i": 99})


def test_verify_daemon_auto_reanchors_benign_staleness(tmp_path: Path, monkeypatch) -> None:
    """With --auto-anchor, a STALE-but-valid-prefix anchor is re-signed
    against the current tip instead of alerting (keyless background
    processes legitimately append without re-anchoring)."""
    from msb_v3.uac.chain_anchor import _verify_daemon

    db = tmp_path / "audit.db"
    seed = generate_seed()
    _stale_chain(db, seed)
    state_dir = tmp_path / "state"
    monkeypatch.setenv(KEY_ENV, seed.hex())
    monkeypatch.setenv("MSB_ANCHOR_STATE_DIR", str(state_dir))

    assert _verify_daemon(str(db), notify=False, auto_anchor=True) == 0
    state = json.loads((state_dir / "chain_anchor.json").read_text())
    assert state["healthy"] is True
    assert state["auto_reanchored"] is True
    assert state["stale"] is False
    assert state["record_count"] == 4  # the appended record is now covered


def test_verify_daemon_without_auto_anchor_alerts_on_staleness(tmp_path: Path, monkeypatch) -> None:
    from msb_v3.uac.chain_anchor import _verify_daemon

    db = tmp_path / "audit.db"
    seed = generate_seed()
    _stale_chain(db, seed)
    state_dir = tmp_path / "state"
    monkeypatch.setenv(KEY_ENV, seed.hex())
    monkeypatch.setenv("MSB_ANCHOR_STATE_DIR", str(state_dir))

    assert _verify_daemon(str(db), notify=False, auto_anchor=False) == 2
    state = json.loads((state_dir / "chain_anchor.json").read_text())
    assert state["healthy"] is False
    assert state["auto_reanchored"] is False


def test_verify_daemon_auto_anchor_still_alerts_on_replacement(tmp_path: Path, monkeypatch) -> None:
    """A whole-DB replacement (T7) must alert even with --auto-anchor — the
    anchored tip is absent, not merely stale."""
    from msb_v3.uac.chain_anchor import _verify_daemon

    db = tmp_path / "audit.db"
    chain = make_chain(db, 5)
    seed = generate_seed()
    ChainAnchor(seed=seed).anchor(chain)
    # attacker swaps in an older internally-valid chain
    make_chain(tmp_path / "fresh.db", 3)
    os.replace(tmp_path / "fresh.db", db)
    state_dir = tmp_path / "state"
    monkeypatch.setenv(KEY_ENV, seed.hex())
    monkeypatch.setenv("MSB_ANCHOR_STATE_DIR", str(state_dir))

    assert _verify_daemon(str(db), notify=False, auto_anchor=True) == 2
    state = json.loads((state_dir / "chain_anchor.json").read_text())
    assert state["healthy"] is False
    assert state["auto_reanchored"] is False
    assert "replacement" in (state["reason"] or "")


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
    make_chain(tmp_path / "swap.db", 1)
    os.replace(tmp_path / "swap.db", tmp_path / "audit.db")
    assert verifier.verify(chain)["valid"] is False


def test_notary_log_verifies_latest_entry(tmp_path: Path) -> None:
    """An append-only notary log of signed snapshots must verify against the
    live chain, with the tip present in the chain."""
    chain = make_chain(tmp_path / "audit.db", 4)
    signer = ChainAnchor(seed=generate_seed())
    signer.anchor(chain)
    log = tmp_path / "notary.jsonl"
    signer.notarize(chain, log)  # first entry
    AuditChain(str(tmp_path / "audit.db"), allow_keyless=True).append("test", "normal", {"i": 99})
    signer.anchor(chain)
    signer.notarize(chain, log)  # second (latest) entry

    result = signer.verify_notary(chain, log)
    assert result["valid"] is True
    assert result["entry_count"] == 2
    assert result["record_count"] == 5


def test_notary_log_detects_whole_db_rollback(tmp_path: Path) -> None:
    """The out-of-band story: even if the local anchor file is replaced along
    with the DB, the notary's last signed tip is absent from the rolled-back
    chain — the notary catches what the anchor file alone cannot."""
    chain = make_chain(tmp_path / "audit.db", 4)
    signer = ChainAnchor(seed=generate_seed())
    signer.anchor(chain)
    log = tmp_path / "notary.jsonl"
    signer.notarize(chain, log)

    # attacker replaces the DB (and would also replace the local anchor file)
    make_chain(tmp_path / "fresh.db", 2)
    os.replace(tmp_path / "fresh.db", tmp_path / "audit.db")

    result = signer.verify_notary(chain, log)
    assert result["valid"] is False
    assert "notarized tip is not in the live chain" in result["reason"]


def test_notary_log_detects_tamper_and_wrong_key(tmp_path: Path) -> None:
    chain = make_chain(tmp_path / "audit.db", 3)
    signer = ChainAnchor(seed=generate_seed())
    signer.anchor(chain)
    log = tmp_path / "notary.jsonl"
    signer.notarize(chain, log)

    # tamper: flip the signature in the last entry
    lines = log.read_text().splitlines()
    entry = json.loads(lines[-1])
    entry["anchor"]["signature"] = "f" * 128
    lines[-1] = json.dumps(entry, sort_keys=True, separators=(",", ":"))
    log.write_text("\n".join(lines))
    assert signer.verify_notary(chain, log)["valid"] is False

    # wrong key: another signer cannot validate the log
    log.write_text("\n".join(lines[:-1]) + "\n")
    other = ChainAnchor(seed=generate_seed())
    assert other.verify_notary(chain, log)["valid"] is False


def test_notary_missing_or_empty_log(tmp_path: Path) -> None:
    chain = make_chain(tmp_path / "audit.db", 2)
    signer = ChainAnchor(seed=generate_seed())
    assert signer.verify_notary(chain, tmp_path / "missing.jsonl")["valid"] is False
    empty = tmp_path / "empty.jsonl"
    empty.write_text("\n")
    assert signer.verify_notary(chain, empty)["valid"] is False


def test_factory_plain_without_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import msb_v3.uac.chain_anchor as mod

    monkeypatch.delenv(KEY_ENV, raising=False)
    monkeypatch.setattr(mod, "_default_key_path", lambda: tmp_path / "none.key")
    assert isinstance(anchored_chain_from_env(), AuditChain)


def test_factory_anchored_with_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import msb_v3.uac.audit_chain as audit_mod
    import msb_v3.uac.chain_anchor as mod

    # isolate the DEFAULT chain so the wrapper anchors in tmp, never the live DB
    monkeypatch.setattr(audit_mod, "_AUDIT_DB", tmp_path / "audit.db")
    monkeypatch.setenv(KEY_ENV, generate_seed().hex())
    monkeypatch.setattr(mod, "_default_key_path", lambda: tmp_path / "none.key")
    result = anchored_chain_from_env()
    assert isinstance(result, AnchoredAuditChain)
    assert result.verify_anchored()["valid"] is True


def test_wrapper_refuses_to_clobber_foreign_key_anchor(tmp_path: Path) -> None:
    """The live-incident regression: an init path with a DIFFERENT key must
    raise, never silently rotate the anchored key (found in production when a
    test process re-anchored the live chain with a random key)."""
    chain = make_chain(tmp_path / "audit.db", 2)
    original = ChainAnchor(seed=generate_seed())
    original.anchor(chain)

    intruder = ChainAnchor(seed=generate_seed())  # different key
    with pytest.raises(ValueError, match="refusing to clobber"):
        AnchoredAuditChain(chain, intruder)

    # the anchored key was NOT rotated
    assert original.verify(chain)["valid"] is True


def test_wrapper_init_with_same_key_is_fine(tmp_path: Path) -> None:
    chain = make_chain(tmp_path / "audit.db", 2)
    anchor = ChainAnchor(seed=generate_seed())
    anchor.anchor(chain)
    # same key -> construction succeeds and verification stays valid
    wrapped = AnchoredAuditChain(chain, anchor)
    assert wrapped.verify_anchored()["valid"] is True
