"""Audited-provider P-256 primitives for the node protocol.

The wire protocol uses raw uncompressed P-256 public keys and fixed-width
(r || s) signatures. Private keys remain owned by the platform key provider;
this module only handles protocol-compatible signing and verification. The
``cryptography`` provider performs the elliptic-curve operations instead of
the former dependency-free prototype implementation.
"""

from __future__ import annotations

import secrets
from typing import Tuple

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils

# NIST P-256 order, used only for raw signature range and low-S validation.
_N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551


def _private_key(value: int) -> ec.EllipticCurvePrivateKey:
    if not 1 <= value < _N:
        raise ValueError("invalid private key")
    return ec.derive_private_key(value, ec.SECP256R1())


def public_key_bytes(private_key: int) -> bytes:
    """Return an uncompressed X9.63 P-256 public key."""
    return _private_key(private_key).public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )


def generate_keypair() -> Tuple[int, bytes]:
    """Generate a protocol-compatible P-256 private scalar and public key."""
    private_key = secrets.randbelow(_N - 1) + 1
    return private_key, public_key_bytes(private_key)


def sign(private_key: int, message: bytes) -> bytes:
    """Return a fixed-width raw ECDSA signature (r || low-S)."""
    der_signature = _private_key(private_key).sign(message, ec.ECDSA(hashes.SHA256()))
    r, s = utils.decode_dss_signature(der_signature)
    s = min(s, _N - s)
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """Verify a fixed-width raw ECDSA signature via ``cryptography``."""
    if len(public_key) != 65 or public_key[0] != 4 or len(signature) != 64:
        return False
    r = int.from_bytes(signature[:32], "big")
    s = int.from_bytes(signature[32:], "big")
    if not (1 <= r < _N and 1 <= s <= _N // 2):
        return False
    try:
        key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), public_key)
        der_signature = utils.encode_dss_signature(r, s)
        key.verify(der_signature, message, ec.ECDSA(hashes.SHA256()))
    except (InvalidSignature, ValueError, TypeError):
        return False
    return True
