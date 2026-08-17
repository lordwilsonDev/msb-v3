"""Signing-backend seam + algorithm-agnostic anchor verification (security-#1).

Hermetic: the P-256 path is exercised with a software ECDSA key, which shares
the exact wire format (uncompressed public point + DER ECDSA signature) with
Secure Enclave / YubiKey PIV — so a hardware key plugs in without any change
to the anchor/notary code. The Secure Enclave backend's subprocess glue is
exercised against a FAKE ``secenclave-tool`` that implements the same JSON
contract (including raw X9.62 r||s output), so the X9.62 -> DER conversion
and arg parsing are covered without an enclave. Unprovisioned hardware
backends fail closed; enrollment is asserted against a label that can never
exist, so the tests are deterministic on every machine.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

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

# Fake secenclave-tool: implements the exact JSON contract of the real Swift
# helper (scripts/secenclave/secenclave.swift) — including raw X9.62 r||s
# signatures — using a software P-256 key persisted per label. This proves the
# backend's arg parsing, output parsing, and _x962_to_der conversion without
# an enclave.
FAKE_SECENCLAVE_TOOL = r"""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils

STATE = Path(os.environ["FAKE_TOOL_STATE"])
CURVE = ec.SECP256R1()

def out(d):
    print(json.dumps(d)); sys.exit(0)

def fail(m):
    print(json.dumps({"ok": "false", "error": m})); sys.exit(1)

def keyfile(label):
    return STATE / (label + ".pem")

def load(label):
    p = keyfile(label)
    if not p.exists():
        fail("no Secure Enclave key labeled '" + label + "'")
    return serialization.load_pem_private_key(p.read_bytes(), password=None)

def public_hex(priv):
    nums = priv.public_key().public_numbers()
    size = 32
    return ("04" + nums.x.to_bytes(size, "big").hex() + nums.y.to_bytes(size, "big").hex())

cmd = sys.argv[1]
args = sys.argv[2:]
label = "msb-chain-anchor"
hexmsg = ""
i = 0
while i < len(args):
    if args[i] == "--label" and i + 1 < len(args):
        label = args[i + 1]; i += 1
    elif args[i] == "--hex" and i + 1 < len(args):
        hexmsg = args[i + 1]; i += 1
    i += 1

if cmd == "create":
    if keyfile(label).exists():
        fail("key '" + label + "' already exists (use --force to re-create)")
    priv = ec.generate_private_key(CURVE)
    keyfile(label).write_bytes(priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    out({"ok": "true", "public_key": public_hex(priv), "label": label})
elif cmd == "public":
    priv = load(label)
    out({"ok": "true", "public_key": public_hex(priv), "label": label})
elif cmd == "sign":
    priv = load(label)
    message = bytes.fromhex(hexmsg)
    der = priv.sign(message, ec.ECDSA(hashes.SHA256()))
    r, s = utils.decode_dss_signature(der)
    size = 32
    raw = r.to_bytes(size, "big") + s.to_bytes(size, "big")  # X9.62 r||s
    out({"ok": "true", "signature": raw.hex(), "label": label})
elif cmd == "delete":
    keyfile(label).unlink(missing_ok=True)
    out({"ok": "true", "deleted": label})
else:
    fail("unknown command: " + cmd)
"""


@pytest.fixture
def fake_secenclave(tmp_path: Path, monkeypatch) -> Path:
    script = tmp_path / "fake-secenclave-tool"
    script.write_text(FAKE_SECENCLAVE_TOOL)
    script.chmod(0o755)
    state = tmp_path / "se-state"
    state.mkdir()
    monkeypatch.setenv("FAKE_TOOL_STATE", str(state))
    monkeypatch.setenv("MSB_SECURE_ENCLAVE_TOOL", str(script))
    return script


def _enroll(label: str, tool: Path) -> None:
    proc = subprocess.run([str(tool), "create", "--label", label], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout


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
    # A label that can never be enrolled makes this deterministic on every
    # machine (including a Mac with a real tool + enrolled default key).
    se = SecureEnclaveBackend(label="unprovisioned-test-key")
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
    # unavailable must never degrade silently to an unsigned chain. A bogus
    # label keeps this deterministic even on a provisioned Mac.
    monkeypatch.setenv("MSB_CHAIN_ANCHOR_BACKEND", "secure-enclave")
    monkeypatch.setenv("MSB_SECURE_ENCLAVE_KEY_LABEL", "unprovisioned-test-key")
    with pytest.raises(SigningBackendUnavailable):
        ChainAnchor.from_env()

    monkeypatch.setenv("MSB_CHAIN_ANCHOR_BACKEND", "software")
    monkeypatch.setenv("MSB_CHAIN_ANCHOR_KEY", bytes(range(32)).hex())
    anchor = ChainAnchor.from_env()
    assert anchor._algorithm == "ed25519"


def test_secure_enclave_backend_signs_via_tool(fake_secenclave):
    backend = SecureEnclaveBackend(label="msb-chain-anchor")
    assert backend.available() is False  # not enrolled yet

    _enroll("msb-chain-anchor", fake_secenclave)
    assert backend.available() is True
    assert backend.hardware_assurance() == "secure-enclave"

    msg = b"the canonicalized anchor snapshot"
    signature = backend.sign(msg)
    assert len(signature) > 0  # DER ECDSA (converted from the tool's raw r||s)
    assert verify_signature(msg, signature, backend.public_key_bytes(), SECP256R1) is True
    assert verify_signature(b"other", signature, backend.public_key_bytes(), SECP256R1) is False


def test_secure_enclave_backend_anchors_notarizes_and_verifies(fake_secenclave, tmp_path):
    _enroll("msb-chain-anchor", fake_secenclave)
    chain = AuditChain(str(tmp_path / "audit.db"))
    chain.append("s", "e", {"n": 1})

    anchor = ChainAnchor(backend=SecureEnclaveBackend(label="msb-chain-anchor"))
    anchor.anchor(chain)
    assert anchor.verify(chain)["valid"] is True

    notary = tmp_path / "notary.jsonl"
    anchor.notarize(chain, notary)
    assert anchor.verify_notary(chain, notary)["valid"] is True

    record = json.loads((tmp_path / "chain_anchor.json").read_text())
    assert record["key_algorithm"] == "secp256r1"


def test_anchored_chain_from_env_uses_hardware_backend(fake_secenclave, tmp_path, monkeypatch):
    """A configured non-software backend anchors even without a seed env — the
    fix for secure-enclave deployments: anchored_chain_from_env used to return
    a PLAIN (unanchored) chain when only the backend was configured."""
    import msb_v3.uac.audit_chain as audit_mod
    from msb_v3.uac.chain_anchor import AnchoredAuditChain, anchored_chain_from_env

    monkeypatch.setattr(audit_mod, "_AUDIT_DB", tmp_path / "audit.db")
    _enroll("msb-chain-anchor", fake_secenclave)
    monkeypatch.setenv("MSB_CHAIN_ANCHOR_BACKEND", "secure-enclave")

    result = anchored_chain_from_env()
    assert isinstance(result, AnchoredAuditChain)
    assert result.verify_anchored()["valid"] is True


def test_anchored_chain_from_env_fails_closed_unprovisioned(fake_secenclave, monkeypatch):
    """Anchoring requested via a hardware backend but unavailable must raise,
    never silently degrade to an unsigned chain."""
    from msb_v3.uac.chain_anchor import anchored_chain_from_env

    monkeypatch.setenv("MSB_CHAIN_ANCHOR_BACKEND", "secure-enclave")
    monkeypatch.setenv("MSB_SECURE_ENCLAVE_KEY_LABEL", "unprovisioned-test-key")
    with pytest.raises(SigningBackendUnavailable):
        anchored_chain_from_env()


def test_secure_enclave_backend_fail_closed_without_tool(monkeypatch):
    monkeypatch.setenv("MSB_SECURE_ENCLAVE_TOOL", "/nonexistent/secenclave-tool")
    backend = SecureEnclaveBackend()
    assert backend.available() is False
    assert "secenclave-tool" in backend.unavailable_reason()
    with pytest.raises(SigningBackendUnavailable, match="failed to run"):
        backend.sign(b"x")
