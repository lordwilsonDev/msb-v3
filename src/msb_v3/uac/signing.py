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

``SecureEnclaveBackend`` talks to a small Swift helper
(``scripts/secenclave/secenclave.swift``, built by ``build.sh``) over a JSON
CLI — no Python dependency, and the same wire format the software backend
uses, so hermetic tests exercise the full glue with a fake tool and the real
enclave plugs in without touching the anchor/notary code. Until a key is
enrolled it fails closed: ``available()`` is False with a provisioning reason
and ``sign()`` raises ``SigningBackendUnavailable``. Enrollment on macOS 12+
requires the ``keychain-access-groups`` entitlement (an Xcode-signed build;
see the backend docstring and docs/operations/secure-enclave-anchor.md).

Signature encoding contract (must be kept consistent across backends):

  * ed25519    — raw 64-byte signature, 32-byte public key.
  * secp256r1  — DER-encoded ECDSA over SHA-256(message), 65-byte uncompressed
    public point (``0x04 || X || Y``).
  * secp384r1  — same as secp256r1 but 97-byte point (48-byte coordinates).
"""

from __future__ import annotations

import json
import os
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path
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

    Talks to the ``secenclave-tool`` helper (scripts/secenclave/secenclave.swift,
    built by scripts/secenclave/build.sh) over its JSON CLI. The private key is
    generated inside the enclave and NEVER leaves it; the tool persists a
    reference to it in the keychain under ``MSB_SECURE_ENCLAVE_KEY_LABEL``
    (default ``msb-chain-anchor``) with AfterFirstUnlock + privateKeyUsage
    access control, so the unattended launchd notary/verify jobs can sign once
    the operator has unlocked the Mac after boot.

    Provisioning note (macOS 12+): persisting an enclave key requires the
    ``keychain-access-groups`` entitlement, which macOS validates against an
    embedded provisioning profile, not a bare signature. Unsigned and
    ad-hoc-signed binaries both fail with errSecMissingEntitlement (-34018).
    The tool must therefore be built and signed inside an Xcode project with
    the Keychain Sharing capability (free Apple ID) — see
    docs/operations/secure-enclave-anchor.md for the one-time enrollment steps.
    Until then this backend fails closed: ``available()`` is False and
    ``sign()`` raises with the exact completion steps.

    Signature contract (kept identical to the software P-256 backend): the
    tool returns Apple's raw X9.62 r||s; ``_x962_to_der`` converts it to the
    DER encoding ``verify_signature`` accepts.
    """

    algorithm = SECP256R1

    def __init__(self, label: str = "") -> None:
        self._label = label or os.getenv("MSB_SECURE_ENCLAVE_KEY_LABEL", "msb-chain-anchor")
        self._tool = _resolve_secenclave_tool()

    def _run(self, *args: str) -> dict:
        if self._tool is None:
            raise SigningBackendUnavailable(self.unavailable_reason())
        try:
            proc = subprocess.run(
                [self._tool, *args], capture_output=True, text=True, timeout=30
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            raise SigningBackendUnavailable(
                f"secure-enclave tool failed to run ({self._tool}): {exc}"
            ) from exc
        try:
            out = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise SigningBackendUnavailable(
                f"secure-enclave tool returned unparseable output: {proc.stdout[:200]!r}"
            ) from exc
        if proc.returncode != 0 or out.get("ok") not in (True, "true"):
            raise SigningBackendUnavailable(
                f"secure-enclave: {out.get('error', 'unknown error (rc=%d)' % proc.returncode)}"
            )
        return out

    def available(self) -> bool:
        if self._tool is None:
            return False
        try:
            self._run("public", "--label", self._label)
            return True
        except SigningBackendUnavailable:
            return False

    def unavailable_reason(self) -> str:
        if self._tool is None:
            return (
                "secure-enclave backend not provisioned: secenclave-tool not found — build it "
                "with scripts/secenclave/build.sh (macOS only)"
            )
        return (
            "secure-enclave backend not provisioned: no enclave key labeled "
            f"{self._label!r} — enroll with ~/.local/bin/secenclave-tool create --label "
            f"{self._label} (the tool must be provisioning-profile signed; see "
            "docs/operations/secure-enclave-anchor.md)"
        )

    def public_key_bytes(self) -> bytes:
        out = self._run("public", "--label", self._label)
        try:
            return bytes.fromhex(out["public_key"])
        except (KeyError, ValueError) as exc:
            raise SigningBackendUnavailable(
                "secure-enclave: tool returned a malformed public key"
            ) from exc

    def sign(self, message: bytes) -> bytes:
        out = self._run("sign", "--label", self._label, "--hex", message.hex())
        try:
            raw = bytes.fromhex(out["signature"])
        except (KeyError, ValueError) as exc:
            raise SigningBackendUnavailable(
                "secure-enclave: tool returned a malformed signature"
            ) from exc
        return _x962_to_der(raw)

    def hardware_assurance(self) -> str:
        return "secure-enclave"


def _resolve_secenclave_tool() -> Optional[str]:
    """Locate the secenclave-tool binary: MSB_SECURE_ENCLAVE_TOOL, then
    ~/.local/bin/secenclave-tool, then <repo>/scripts/secenclave-tool."""
    env = os.getenv("MSB_SECURE_ENCLAVE_TOOL", "").strip()
    if env:
        return env
    candidates = [
        Path.home() / ".local" / "bin" / "secenclave-tool",
        Path(__file__).resolve().parents[3] / "scripts" / "secenclave-tool",
    ]
    for cand in candidates:
        if cand.exists() and os.access(cand, os.X_OK):
            return str(cand)
    return None


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
