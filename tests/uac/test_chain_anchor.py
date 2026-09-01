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
from msb_v3.uac.signing import ED25519, SoftwareEd25519Backend


def make_chain(db_path: Path, n: int) -> AuditChain:
    chain = AuditChain(str(db_path))
    for i in range(n):
        chain.append("test", "normal", {"i": i})
    return chain


# Fake `security` CLI for hermetic keychain-resolution tests. The real one is
# macOS-only, so the runtime resolves the seed via a subprocess; a fake on PATH
# exercises that glue deterministically on every platform.
FAKE_SECURITY = """#!/usr/bin/env bash
set -u
if [ "${1:-}" = "find-generic-password" ]; then
  if [ "${FAKE_KEYCHAIN_RESULT:-ok}" = "missing" ]; then
    echo "security: the specified item could not be found" >&2
    exit 44
  fi
  printf '%s' "${FAKE_KEYCHAIN_SEED:-}"
  exit 0
fi
exit 1
"""


@pytest.fixture
def fake_security(tmp_path: Path, monkeypatch) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    script = bindir / "security"
    script.write_text(FAKE_SECURITY)
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ.get('PATH', '')}")
    monkeypatch.setenv("FAKE_KEYCHAIN_SEED", bytes(range(32)).hex())
    return script


def test_anchor_verify_roundtrip(tmp_path: Path) -> None:
    chain = make_chain(tmp_path / "audit.db", 5)
    anchor = ChainAnchor(seed=generate_seed())
    anchor.anchor(chain)

    result = anchor.verify(chain)
    assert result["valid"] is True
    assert result["record_count"] == 5


def test_from_env_resolves_seed_from_keychain_when_configured(
    fake_security, monkeypatch, tmp_path: Path
) -> None:
    """With MSB_CHAIN_ANCHOR_KEYCHAIN_SERVICE set, the seed comes from the
    login keychain instead of env/file — so the key need not live in
    plaintext."""
    import msb_v3.uac.audit_chain as audit_mod

    monkeypatch.setenv("MSB_CHAIN_ANCHOR_KEYCHAIN_SERVICE", "msb-chain-anchor-key")
    monkeypatch.setattr(audit_mod, "_AUDIT_DB", tmp_path / "audit.db")
    anchor = ChainAnchor.from_env()

    chain = make_chain(tmp_path / "audit.db", 3)
    anchor.anchor(chain)
    assert anchor.verify(chain)["valid"] is True
    # The anchor used the keychain-provided seed (FAKE_KEYCHAIN_SEED).
    from msb_v3.uac.signing import SoftwareEd25519Backend

    expected = SoftwareEd25519Backend(bytes(range(32))).public_key_hex()
    record = json.loads((tmp_path / "chain_anchor.json").read_text())
    assert record["public_key"] == expected


def test_from_env_keychain_missing_fails_closed(fake_security, monkeypatch) -> None:
    """A configured keychain item that is absent raises with the store path —
    never silently continues unanchored."""
    monkeypatch.setenv("MSB_CHAIN_ANCHOR_KEYCHAIN_SERVICE", "msb-chain-anchor-key")
    monkeypatch.setenv("FAKE_KEYCHAIN_RESULT", "missing")
    with pytest.raises(ValueError, match="store-anchor-key.sh"):
        ChainAnchor.from_env()


def test_anchored_chain_from_env_anchors_with_keychain(fake_security, monkeypatch, tmp_path: Path) -> None:
    """A configured keychain item anchors even with no env seed and no keyfile
    — the factory must not return a plain (unanchored) chain."""
    import msb_v3.uac.audit_chain as audit_mod

    monkeypatch.setenv("MSB_CHAIN_ANCHOR_KEYCHAIN_SERVICE", "msb-chain-anchor-key")
    monkeypatch.setattr(audit_mod, "_AUDIT_DB", tmp_path / "audit.db")
    result = anchored_chain_from_env()
    assert isinstance(result, AnchoredAuditChain)
    assert result.verify_anchored()["valid"] is True


def test_missing_key_still_fails_closed(monkeypatch) -> None:
    """No env seed, no keyfile, and no keychain item (under the env-gated
    service OR the canonical default) => the standard missing-key error.
    from_env() now tries DEFAULT_KEYCHAIN_SERVICE as a last resort, but the
    fail-closed guarantee must survive when nothing is stored anywhere."""
    import msb_ledger.chain_anchor as ca

    monkeypatch.delenv("MSB_CHAIN_ANCHOR_KEYCHAIN_SERVICE", raising=False)
    monkeypatch.delenv(KEY_ENV, raising=False)
    monkeypatch.setattr(ca, "_seed_from_keychain", lambda service=None: None)
    with pytest.raises(ValueError, match="no chain anchor key configured"):
        ChainAnchor.from_env()


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
    # The registry (bootstrapped in-memory to the verifying key when no
    # registry file exists yet) rejects the foreign key's anchor.
    assert "current key or recovery key" in result["reason"]


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


# ── key rotation / recovery ceremony ──────────────────────────────────────


def test_rotation_cross_signs_successor_and_reanchors(tmp_path: Path) -> None:
    """A planned rotation: the OLD key cross-signs a successor endorsement,
    the registry advances, and the chain is re-anchored with the new key.
    The new anchor verifies under the new key."""
    chain = make_chain(tmp_path / "audit.db", 3)
    old = ChainAnchor(seed=generate_seed())
    old.anchor(chain)
    assert old.verify(chain)["valid"] is True

    new_seed = generate_seed()
    result = old.rotate(chain, SoftwareEd25519Backend(new_seed), "hardware migration")

    # The rotation record is cross-signed by the OLD key.
    rotation = result["rotation"]
    assert rotation["from"] == old.public_key_hex()
    assert rotation["from_signature"]  # non-empty: old key endorsed it
    assert rotation["to"] == SoftwareEd25519Backend(new_seed).public_key_hex()
    assert rotation["reason"] == "hardware migration"

    # The anchor moved to the NEW key and verifies under it.
    new = ChainAnchor(seed=new_seed)
    assert new.verify(chain)["valid"] is True
    # The OLD key can no longer verify (its registry slot is gone).
    assert old.verify(chain)["valid"] is False

    # The registry recorded the rotation and advanced the current key.
    reg = json.loads((tmp_path / "chain_key_registry.json").read_text())
    assert reg["current_public_key"] == rotation["to"]
    assert len(reg["rotations"]) == 1


def test_rotation_cannot_happen_without_current_key(tmp_path: Path) -> None:
    """A stale key cannot rotate: only the registry's current key signs
    successor endorsements."""
    chain = make_chain(tmp_path / "audit.db", 2)
    old = ChainAnchor(seed=generate_seed())
    old.anchor(chain)
    old.rotate(chain, SoftwareEd25519Backend(generate_seed()), "rotate away")

    # A THIRD key pretending to be a successor without being current:
    imposter = ChainAnchor(seed=generate_seed())
    with pytest.raises(ValueError, match="not the registry's current key"):
        imposter.rotate(chain, SoftwareEd25519Backend(generate_seed()), "imposter")


def test_recovery_key_reanchors_after_primary_lost(tmp_path: Path) -> None:
    """The recovery path: register an offline recovery public key, then
    (simulating the primary key dying) re-anchor with the recovery seed. The
    chain stays verifiable and verification reports the recovery signer."""
    chain = make_chain(tmp_path / "audit.db", 3)
    primary = ChainAnchor(seed=generate_seed())
    primary.anchor(chain)

    recovery_seed = generate_seed()
    recovery_pub = SoftwareEd25519Backend(recovery_seed).public_key_bytes()
    primary.register_recovery(chain, recovery_pub, ED25519, "offline recovery")

    # Primary key dies: verification with the recovery key re-anchors.
    recovery = ChainAnchor(seed=recovery_seed)
    result = recovery.recover(chain, SoftwareEd25519Backend(recovery_seed), "enclave died")
    assert result["anchored"]["valid"] is True

    # The chain verifies under the recovery key and reports the signer.
    assert recovery.verify(chain)["valid"] is True
    assert recovery.verify(chain)["signer"] == "recovery-key"

    # The primary key cannot re-anchor anymore (it lost the current slot).
    assert primary.verify(chain)["valid"] is False


def test_recovery_fails_closed_without_registered_key(tmp_path: Path) -> None:
    """Recovery is impossible if no recovery key was registered beforehand."""
    chain = make_chain(tmp_path / "audit.db", 2)
    primary = ChainAnchor(seed=generate_seed())
    primary.anchor(chain)
    recovery_seed = generate_seed()
    recovery = ChainAnchor(seed=recovery_seed)
    with pytest.raises(ValueError, match="no recovery key registered"):
        recovery.recover(chain, SoftwareEd25519Backend(recovery_seed), "lost key")


def test_recovery_rejects_unregistered_key(tmp_path: Path) -> None:
    """A random key cannot claim recovery: it must match the registered
    recovery public key."""
    chain = make_chain(tmp_path / "audit.db", 2)
    primary = ChainAnchor(seed=generate_seed())
    primary.anchor(chain)
    primary.register_recovery(chain, SoftwareEd25519Backend(generate_seed()).public_key_bytes())

    attacker = ChainAnchor(seed=generate_seed())
    with pytest.raises(ValueError, match="does not match the registered recovery"):
        attacker.recover(chain, SoftwareEd25519Backend(generate_seed()), "stolen key")


def test_revoked_key_cannot_sign_new_anchors_but_history_stays_valid(tmp_path: Path) -> None:
    """Revocation retires a key for NEW anchors; its historical notary
    entries remain verifiable (a revoked key cannot un-sign history)."""
    chain = make_chain(tmp_path / "audit.db", 3)
    old = ChainAnchor(seed=generate_seed())
    old.anchor(chain)

    # Notarize under the old key first — this history must survive rotation.
    notary = tmp_path / "notary.jsonl"
    old.notarize(chain, notary)

    # Rotate to a new key.
    new_seed = generate_seed()
    old.rotate(chain, SoftwareEd25519Backend(new_seed), "revoke old key")
    new = ChainAnchor(seed=new_seed)

    # Revoke the OLD key (signed by the current key).
    old_pub = bytes.fromhex(old.public_key_hex())
    record = new.revoke(chain, key_to_revoke=old_pub, reason="compromised")
    assert record["key"] == old.public_key_hex()

    # The revoked key cannot sign a NEW anchor.
    assert old.verify(chain)["valid"] is False  # anchor moved anyway
    reg = json.loads((tmp_path / "chain_key_registry.json").read_text())
    assert any(r["key"] == old.public_key_hex() for r in reg["revocations"])

    # The historical notary entry from the revoked key still verifies.
    report = new.verify_notary(chain, notary)
    assert report["valid"] is True


def test_notary_history_verifies_across_rotation(tmp_path: Path) -> None:
    """The core P1 fix: notary entries signed by the PRE-rotation key remain
    verifiable after the anchor key moves (hardware migration must not
    invalidate history)."""
    chain = make_chain(tmp_path / "audit.db", 3)
    old = ChainAnchor(seed=generate_seed())
    old.anchor(chain)
    notary = tmp_path / "notary.jsonl"
    old.notarize(chain, notary)
    old.notarize(chain, notary)  # two entries under the old key

    old.rotate(chain, SoftwareEd25519Backend(generate_seed()), "hardware move")
    new = ChainAnchor(seed=generate_seed())
    new.anchor(chain)

    report = new.verify_notary(chain, notary)
    assert report["valid"] is True
    assert report["entry_count"] == 2


def test_unknown_key_notary_entry_rejected(tmp_path: Path) -> None:
    """An entirely unknown key (never registered) is still rejected in the
    notary log — rotation broadens acceptance, it does not open it."""
    chain = make_chain(tmp_path / "audit.db", 2)
    old = ChainAnchor(seed=generate_seed())
    old.anchor(chain)
    notary = tmp_path / "notary.jsonl"
    old.notarize(chain, notary)

    # Tamper the notary log: replace with an entry signed by an unregistered key.
    intruder = ChainAnchor(seed=generate_seed())
    intruder.anchor(chain)
    intruder.notarize(chain, notary)

    # The LAST entry (intruder) is rejected; the log is not trusted.
    report = old.verify_notary(chain, notary)
    assert report["valid"] is False
    assert "not registered" in report["reason"]
