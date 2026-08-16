"""Signing-backend seam + algorithm-agnostic anchor verification (security-#1).

Hermetic: the P-256 path is exercised with a software ECDSA key, which shares
the exact wire format (uncompressed public point + DER ECDSA signature) with
Secure Enclave / YubiKey PIV — so a hardware key plugs in without any change
to the anchor/notary code. Hardware backends themselves are asserted to fail
closed (unavailable) until provisioned.
"""

from __future__ import annotations

import json

import pytest

from msb_v3.uac.audit_chain import AuditChain
from msb_v3.uac.chain_anchor import ChainAnchor, generate_seed
from msb_v3.uac.signing import (
    ED25519,
    SECP256R1,
    SecureEnclaveBackend,
    SigningBackendUnavailable,
    SoftwareEcdsaBackend,
    SoftwareEd25519Backend,
    YubiKeyPivBackend,
    build_backend,
    verify_signature,
)


def test_verify_signature_dispatches_by_algorithm():
    msg = b"the canonicalized anchor snapshot"
    ed = SoftwareEd25519Backend(bytes(range(32)))
    assert verify_signature(msg, ed.sign(msg), ed.public_key_bytes(), ED25519) is True
    assert verify_signature(b"other", ed.sign(msg), ed.public_key_bytes(), ED25519) is False

    p256 = SoftwareEcdsaBackend(SECP256R1)
    assert verify_signature(msg, p256.sign(msg), p256.public_key_bytes(), SECP256R1) is True
    # Cross-algorithm must never verify (ed25519 with a P-256 key -> False).
    assert verify_signature(msg, p256.sign(msg), p256.public_key_bytes(), ED25519) is False
    # Unknown algorithms raise rather than returning a wrong False.
    with pytest.raises(ValueError, match="unsupported"):
        verify_signature(msg, b"", b"", "rsa2048")


def test_p256_backend_anchors_notarizes_and_verifies(tmp_path):
    chain = AuditChain(str(tmp_path / "audit.db"))
    for i in range(3):
        chain.append("s", "e", {"n": i})

    backend = SoftwareEcdsaBackend(SECP256R1)
    anchor = ChainAnchor(backend=backend)
    anchor.anchor(chain)
    assert anchor.verify(chain)["valid"] is True

    notary = tmp_path / "notary.jsonl"
    anchor.notarize(chain, notary)
    assert anchor.verify_notary(chain, notary)["valid"] is True

    # The anchor record self-describes its key algorithm.
    record = json.loads((tmp_path / "chain_anchor.json").read_text())
    assert record["key_algorithm"] == "secp256r1"


def test_ed25519_anchor_without_algorithm_field_still_verifies(tmp_path):
    # Backward compat: anchors written before the key_algorithm field existed
    # default to ed25519 and still verify.
    chain = AuditChain(str(tmp_path / "audit.db"))
    chain.append("s", "e", {})
    anchor = ChainAnchor(seed=generate_seed())
    anchor.anchor(chain)

    record = json.loads((tmp_path / "chain_anchor.json").read_text())
    record.pop("key_algorithm", None)
    (tmp_path / "chain_anchor.json").write_text(json.dumps(record))

    assert anchor.verify(chain)["valid"] is True


def test_anchor_rejects_a_different_key(tmp_path):
    chain = AuditChain(str(tmp_path / "audit.db"))
    chain.append("s", "e", {})
    ChainAnchor(seed=generate_seed()).anchor(chain)

    other = ChainAnchor(seed=generate_seed())
    result = other.verify(chain)
    assert result["valid"] is False
    assert "does not match" in result["reason"]


def test_verify_only_anchor_with_public_key(tmp_path):
    chain = AuditChain(str(tmp_path / "audit.db"))
    chain.append("s", "e", {})
    backend = SoftwareEcdsaBackend(SECP256R1)
    ChainAnchor(backend=backend).anchor(chain)

    verify_only = ChainAnchor(public_key=backend.public_key_bytes(), algorithm=SECP256R1)
    assert verify_only.verify(chain)["valid"] is True
    with pytest.raises(ValueError, match="verify-only"):
        verify_only.anchor(chain)


def test_hardware_backends_fail_closed_until_provisioned():
    se = SecureEnclaveBackend()
    assert se.available() is False
    assert se.unavailable_reason()
    assert se.hardware_assurance() == "secure-enclave"
    with pytest.raises(SigningBackendUnavailable):
        se.sign(b"x")

    yk = YubiKeyPivBackend()
    assert yk.available() is False
    assert yk.hardware_assurance() == "yubikey-piv"
    with pytest.raises(SigningBackendUnavailable):
        yk.sign(b"x")


def test_build_backend_resolves_names():
    assert isinstance(build_backend("software", seed=bytes(32)), SoftwareEd25519Backend)
    assert isinstance(build_backend("secure-enclave"), SecureEnclaveBackend)
    assert isinstance(build_backend("yubikey"), YubiKeyPivBackend)
    with pytest.raises(ValueError, match="unknown"):
        build_backend("bogus")


def test_chain_anchor_from_env_selects_backend(monkeypatch):
    # An unprovisioned hardware backend fails closed: anchoring requested but
    # unavailable must never degrade silently to an unsigned chain.
    monkeypatch.setenv("MSB_CHAIN_ANCHOR_BACKEND", "secure-enclave")
    with pytest.raises(SigningBackendUnavailable):
        ChainAnchor.from_env()

    monkeypatch.setenv("MSB_CHAIN_ANCHOR_BACKEND", "software")
    monkeypatch.setenv("MSB_CHAIN_ANCHOR_KEY", bytes(range(32)).hex())
    anchor = ChainAnchor.from_env()
    assert anchor._algorithm == "ed25519"
