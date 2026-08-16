"""Signing backends for the external chain-tip anchor (security-hardening #1).

The anchor's proof is only as strong as the key that signs it, and today the
key is an on-box Ed25519 seed (env or file) — an attacker who owns the box
owns the key and can forge a fresh anchor over a rewritten chain. This module
introduces a ``SigningBackend`` seam so the key can move OFF the box without
touching the anchor/notary code:

  * ``SoftwareEd25519Backend`` — the current behavior (default; the seed stays
    in ``MSB_CHAIN_ANCHOR_KEY`` / the keyfile).
  * ``SoftwareEcdsaBackend`` — a P-256/P-384 software key. Not off-box; used
    for migration and tests. It produces the SAME wire format (uncompressed
    public point + DER ECDSA signature) as the hardware backends, so it proves
    the non-Ed25519 path end-to-end.
  * ``SecureEnclaveBackend`` — P-256 ECDSA signing inside Apple's Secure
    Enclave (macOS); the private key never leaves the enclave.
  * ``YubiKeyPivBackend`` — P-256/P-384 ECDSA signing on a YubiKey PIV slot.

The anchor record now carries ``key_algorithm`` and verification dispatches on
it, so an Ed25519 anchor and a P-256 (hardware) anchor coexist; an anchor
without the field is treated as Ed25519 (backward compatible).

The hardware backends are **unverified on this box** (no PyObjC / YubiKey in
CI) and fail closed: ``available()`` is False with a provisioning reason, and
``sign()`` raises ``SigningBackendUnavailable`` listing the exact completion
steps. The seam + the P-256 verification + the anchor-format change are the
real, tested deliverable; the hardware ``sign()`` glue is an operator
completion step that needs the optional dependency and a provisioned key.

Signature encoding contract (must be kept consistent across backends):

  * ed25519    — raw 64-byte signature, 32-byte public key.
  * secp256r1  — DER-encoded ECDSA over SHA-256(message), 65-byte uncompressed
    public point (``0x04 || X || Y``).
  * secp384r1  — same as secp256r1 but 97-byte point (48-byte coordinates).
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, utils

# Canonical signature encodings this module verifies.
ED25519 = "ed25519"
SECP256R1 = "secp256r1"
SECP384R1 = "secp384r1"

_SUPPORTED = (ED25519, SECP256R1, SECP384R1)


class SigningBackendUnavailable(RuntimeError):
    """A hardware backend cannot sign right now (unprovisioned / no device)."""


class SigningBackend(ABC):
    """One anchor-signing key source.

    ``sign`` signs the already-canonicalized message; ``public_key_bytes`` is
    the verifying key. ``algorithm`` names the signature encoding so the
    verifier can dispatch without knowing the concrete backend.
    """

    algorithm: str = ED25519

    @abstractmethod
    def sign(self, message: bytes) -> bytes: ...

    @abstractmethod
    def public_key_bytes(self) -> bytes: ...

    def public_key_hex(self) -> str:
        return self.public_key_bytes().hex()

    def hardware_assurance(self) -> str:
        """Where the key lives: software / secure-enclave / yubikey-piv."""
        return "software"

    def available(self) -> bool:
        return True

    def unavailable_reason(self) -> str:
        return ""


# ── software backends --------------------------------------------------------


class SoftwareEd25519Backend(SigningBackend):
    """The current on-box key: a raw 32-byte Ed25519 seed."""

    algorithm = ED25519

    def __init__(self, seed: bytes) -> None:
        if len(seed) != 32:
            raise ValueError("Ed25519 seed must be 32 bytes")
        self._priv = ed25519.Ed25519PrivateKey.from_private_bytes(seed)

    def sign(self, message: bytes) -> bytes:
        return self._priv.sign(message)

    def public_key_bytes(self) -> bytes:
        return self._priv.public_key().public_bytes_raw()


class SoftwareEcdsaBackend(SigningBackend):
    """A software P-256/P-384 key. NOT off-box — used for migration and tests,
    and to prove the hardware wire format before a hardware key is enrolled."""

    def __init__(self, curve: str = SECP256R1) -> None:
        self.algorithm = curve
        self._curve = _curve(curve)
        self._priv = ec.generate_private_key(self._curve)

    def sign(self, message: bytes) -> bytes:
        return self._priv.sign(message, ec.ECDSA(hashes.SHA256()))

    def public_key_bytes(self) -> bytes:
        return _public_point(self._priv.public_key())

    def hardware_assurance(self) -> str:
        return "software"


# ── hardware backends (unverified until provisioned) -------------------------


class SecureEnclaveBackend(SigningBackend):
    """P-256 ECDSA signing inside Apple's Secure Enclave (macOS).

    Completion step (operator, on the Mac, once):
      1. `pip install pyobjc-framework-Security` (add to a hardware-extra, not
         the runtime lock — it is macOS-only).
      2. Generate a non-exportable P-256 key with
         `SecKeyCreateRandomKey` + `kSecAttrTokenIDSecureEnclave` + the label
         `MSB_SECURE_ENCLAVE_KEY_LABEL` (default ``msb-chain-anchor``), stored
         in the keychain.
      3. Implement ``sign`` below: fetch the key with ``SecItemCopyMatching``,
         then ``SecKeyCreateSignature(key, kSecKeyAlgorithmECDSASignatureMessageX962SHA256,
         message, error)`` — which yields a raw r||s (X9.62) signature; convert
         it to DER with ``_x962_to_der`` before returning.
    """

    algorithm = SECP256R1

    def __init__(self, label: str = "") -> None:
        self._label = label or os.getenv("MSB_SECURE_ENCLAVE_KEY_LABEL", "msb-chain-anchor")

    def available(self) -> bool:
        return False  # needs PyObjC + a provisioned enclave key (see docstring)

    def unavailable_reason(self) -> str:
        return (
            "secure-enclave backend not provisioned: install pyobjc-framework-Security "
            f"and enroll a P-256 key labeled {self._label!r} (see uac/signing.py)"
        )

    def public_key_bytes(self) -> bytes:
        raise SigningBackendUnavailable(self.unavailable_reason())

    def sign(self, message: bytes) -> bytes:
        raise SigningBackendUnavailable(self.unavailable_reason())

    def hardware_assurance(self) -> str:
        return "secure-enclave"


class YubiKeyPivBackend(SigningBackend):
    """P-256/P-384 ECDSA signing on a YubiKey PIV slot.

    Completion step (operator): enroll a key into a PIV slot (``ykman piv
    keys generate``) and sign via PKCS#11 (``ykcs11`` / ``pkcs11-tool --sign``
    with ECDSA-SHA256), returning the DER signature. A raw r||s result must be
    converted with ``_x962_to_der``.
    """

    def __init__(self, curve: str = SECP256R1, slot: str = "9a") -> None:
        self.algorithm = curve
        self._slot = slot or os.getenv("MSB_YUBIKEY_PIV_SLOT", "9a")

    def available(self) -> bool:
        # A YubiKey must be present AND a key enrolled; unverifiable here.
        return False

    def unavailable_reason(self) -> str:
        return (
            "yubikey-piv backend not provisioned: enroll a key in PIV slot "
            f"{self._slot!r} and wire PKCS#11 signing (see uac/signing.py)"
        )

    def public_key_bytes(self) -> bytes:
        raise SigningBackendUnavailable(self.unavailable_reason())

    def sign(self, message: bytes) -> bytes:
        raise SigningBackendUnavailable(self.unavailable_reason())

    def hardware_assurance(self) -> str:
        return "yubikey-piv"


# ── algorithm-agnostic verification -----------------------------------------


def _curve(algorithm: str) -> ec.EllipticCurve:
    if algorithm == SECP256R1:
        return ec.SECP256R1()
    if algorithm == SECP384R1:
        return ec.SECP384R1()
    raise ValueError(f"unsupported ECDSA curve: {algorithm}")


def _public_point(public_key: ec.EllipticCurvePublicKey) -> bytes:
    nums = public_key.public_numbers()
    size = (nums.curve.key_size + 7) // 8
    return b"\x04" + nums.x.to_bytes(size, "big") + nums.y.to_bytes(size, "big")


def _x962_to_der(x962: bytes) -> bytes:
    """Apple's Secure Enclave / YubiKey ECDSA produce raw r||s (X9.62); convert
    to the DER encoding ``cryptography`` verifies."""
    size = len(x962) // 2
    r = int.from_bytes(x962[:size], "big")
    s = int.from_bytes(x962[size:], "big")
    return utils.encode_dss_signature(r, s)


def verify_signature(message: bytes, signature: bytes, public_key: bytes, algorithm: str) -> bool:
    """Verify a signature without knowing the concrete backend — dispatch on
    ``algorithm``. Raises ValueError on an unsupported algorithm (never returns
    False for a format we don't understand)."""
    if algorithm == ED25519:
        try:
            ed25519.Ed25519PublicKey.from_public_bytes(public_key).verify(signature, message)
            return True
        except (InvalidSignature, ValueError):
            return False
    if algorithm in (SECP256R1, SECP384R1):
        try:
            point = _public_point_bytes(public_key, algorithm)
            point.public_key().verify(signature, message, ec.ECDSA(hashes.SHA256()))
            return True
        except (InvalidSignature, ValueError):
            return False
    raise ValueError(f"unsupported anchor key algorithm: {algorithm}")


def _public_point_bytes(public_key: bytes, algorithm: str):
    size = 32 if algorithm == SECP256R1 else 48
    if len(public_key) != 1 + 2 * size or public_key[0] != 4:
        raise ValueError(f"public key must be an uncompressed {algorithm} point")
    x = int.from_bytes(public_key[1 : 1 + size], "big")
    y = int.from_bytes(public_key[1 + size :], "big")
    return ec.EllipticCurvePublicNumbers(x, y, _curve(algorithm))


def build_backend(name: str, *, seed: Optional[bytes] = None) -> SigningBackend:
    """Resolve a backend by name (``MSB_CHAIN_ANCHOR_BACKEND``). Software is
    the default; hardware names construct their (unprovisioned) backend."""
    if name == "secure-enclave":
        return SecureEnclaveBackend()
    if name == "yubikey":
        return YubiKeyPivBackend()
    if name == "software":
        if seed is None:
            raise ValueError("software backend requires an Ed25519 seed")
        return SoftwareEd25519Backend(seed)
    raise ValueError(f"unknown chain-anchor backend: {name}")
