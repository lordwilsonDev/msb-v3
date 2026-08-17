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
  * ``YubiKeyPivBackend`` — P-256/P-384 ECDSA signing on a YubiKey PIV slot
    via ``python-pkcs11`` + Yubico's ``libykcs11`` PKCS#11 module.  The
    private key never leaves the YubiKey hardware.

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

``YubiKeyPivBackend`` talks to the YubiKey via ``libykcs11`` (installed by
``brew install yubico-piv-tool``) using the ``python-pkcs11`` wrapper.  The
private key is generated on-device and never leaves the hardware; signing
happens inside the YubiKey's secure element.  Requires a P-256 key + X.509
certificate enrolled in a PIV slot (see docs/operations/yubikey-piv-anchor.md
for the one-time enrollment steps).  Tests exercise the full PKCS#11 glue
with a fake ``libykcs11`` module so no physical YubiKey is needed.

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

    Uses ``python-pkcs11`` with Yubico's ``libykcs11`` PKCS#11 module to:

    1. Read the X.509 certificate from the PIV slot and extract the EC public
       point (uncompressed, same wire format as the software and SE backends).
    2. Sign via the PKCS#11 ``ECDSA_SHA256`` mechanism, which returns a
       DER-encoded signature directly — no X9.62→DER conversion needed.

    Configuration (environment):

    * ``MSB_YUBIKEY_PKCS11_LIB`` — path to ``libykcs11.dylib`` / ``.so``.
      Default search: ``/opt/homebrew/lib/libykcs11.dylib`` (Homebrew macOS)
      then ``/usr/lib/libykcs11.so`` (Linux).
    * ``MSB_YUBIKEY_PIN`` — PIV user PIN.  If absent, falls back to reading
      ``~/.yubikey-pin`` (one line, trimmed).  If neither exists, the PIN is
      prompted interactively — but unattended launchd jobs should set the env
      var or the file.
    * ``MSB_YUBIKEY_PIV_SLOT`` — PIV slot hex (default ``9a`` = PIV
      Authentication; ``9c`` = Signature; ``9d`` = Key Management).

    Enrollment (operator, one-time)::

        # 1. Generate P-256 key in slot 9a
        ykman piv keys generate --algorithm ECCP256 9a /tmp/yubikey-pub.pem
        # 2. Generate self-signed cert (required by PKCS#11 Sign)
        ykman piv certificates generate --subject "CN=msb-chain-anchor" 9a /tmp/yubikey-pub.pem
        # 3. Set PIN if still default
        ykman piv access change-pin --pin 123456 --new-pin <YOUR-PIN>
        # 4. Store PIN for launchd jobs
        echo '<YOUR-PIN>' > ~/.yubikey-pin && chmod 600 ~/.yubikey-pin

    Until a key is enrolled this backend fails closed: ``available()`` is
    False with the enrollment steps and ``sign()`` raises
    ``SigningBackendUnavailable``.

    Signature contract: PKCS#11 ``ECDSA_SHA256`` returns DER, which is the
    exact encoding ``verify_signature`` expects — no conversion.
    """

    # PIN resolution order: env → file → (None = fail-closed for unattended)
    _PIN_FILE = Path.home() / ".yubikey-pin"

    def __init__(self, curve: str = SECP256R1, slot: Optional[str] = None) -> None:
        self.algorithm = curve
        chosen = slot or os.getenv("MSB_YUBIKEY_PIV_SLOT") or "9a"
        self._slot = str(chosen).lower()
        self._lib_path = self._resolve_pkcs11_lib()
        self._pin: Optional[str] = self._resolve_pin()

    # ── resolution helpers ────────────────────────────────────────────────

    @staticmethod
    def _resolve_pkcs11_lib() -> Optional[str]:
        env = os.getenv("MSB_YUBIKEY_PKCS11_LIB", "").strip()
        if env:
            return env
        for candidate in (
            "/opt/homebrew/lib/libykcs11.dylib",  # macOS Homebrew
            "/usr/local/lib/libykcs11.dylib",      # macOS Intel Homebrew
            "/usr/lib/libykcs11.so",                # Linux
        ):
            if os.path.isfile(candidate) and os.access(candidate, os.R_OK):
                return candidate
        return None

    @staticmethod
    def _resolve_pin() -> Optional[str]:
        pin = os.getenv("MSB_YUBIKEY_PIN", "").strip()
        if pin:
            return pin
        if YubiKeyPivBackend._PIN_FILE.is_file():
            return YubiKeyPivBackend._PIN_FILE.read_text().strip() or None
        return None

    # ── PKCS#11 helpers ───────────────────────────────────────────────────

    def _open_session(self):
        """Return a context manager that yields an opened PKCS#11 session
        on the first token (YubiKey).  Raises SigningBackendUnavailable
        if the library is missing, no token is present, or the PIN is bad."""
        if self._lib_path is None:
            raise SigningBackendUnavailable(self.unavailable_reason())
        try:
            import pkcs11 as _pkcs11
        except ImportError as exc:
            raise SigningBackendUnavailable(
                "python-pkcs11 not installed — run: pip install python-pkcs11"
            ) from exc

        try:
            lib = _pkcs11.lib(self._lib_path)
        except Exception as exc:
            raise SigningBackendUnavailable(
                f"could not load PKCS#11 library {self._lib_path}: {exc}"
            ) from exc

        tokens = list(lib.get_tokens())
        if not tokens:
            raise SigningBackendUnavailable(
                "no YubiKey detected —插 one and try again"
            )
        token = tokens[0]

        pin = self._pin
        if pin is None:
            raise SigningBackendUnavailable(
                "no YubiKey PIN configured — set MSB_YUBIKEY_PIN env var or "
                f"create {self._PIN_FILE} with the PIN"
            )

        try:
            session = token.open(rw=True, user_pin=pin)
        except Exception as exc:
            raise SigningBackendUnavailable(
                f"PKCS#11 session open failed (wrong PIN?): {exc}"
            ) from exc
        return session

    # PIV slot hex → the CKA_ID byte used by ykcs11 to tag every object in
    # that slot (docs: developers.yubico.com/yubico-piv-tool/YKCS11).
    def _slot_id(self) -> bytes:
        try:
            return bytes([int(self._slot, 16)])
        except ValueError as exc:
            raise SigningBackendUnavailable(
                f"invalid PIV slot {self._slot!r} — use a hex slot like 9a or 9c"
            ) from exc

    def _find_cert(self, session):
        """Find the X.509 certificate in the PIV slot (by CKA_ID)."""
        from pkcs11 import Attribute, ObjectClass

        # ykcs11 tags cert/key/data objects in a slot with CKA_ID = slot byte.
        certs = list(session.get_objects({
            Attribute.CLASS: ObjectClass.CERTIFICATE,
            Attribute.ID: self._slot_id(),
        }))
        if not certs:
            raise SigningBackendUnavailable(
                f"no certificate in PIV slot {self._slot!r} — enroll one first:\n"
                f"  ykman piv keys generate --algorithm ECCP256 {self._slot} /tmp/pub.pem\n"
                f"  ykman piv certificates generate --subject \"CN=msb-anchor\" {self._slot} /tmp/pub.pem"
            )
        return certs[0]

    def _get_public_key_from_cert(self, session, cert_obj) -> bytes:
        """Extract uncompressed EC point from the X.509 certificate."""
        from cryptography.x509 import load_der_x509_certificate

        cert_der = cert_obj["VALUE"]
        x509_cert = load_der_x509_certificate(cert_der)
        pub_key = x509_cert.public_key()

        if not isinstance(pub_key, ec.EllipticCurvePublicKey):
            raise SigningBackendUnavailable(
                f"PIV certificate in slot {self._slot!r} does not hold an EC key"
            )
        if self.algorithm in (SECP256R1, SECP384R1):
            return _public_point(pub_key)
        raise SigningBackendUnavailable(
            f"unsupported YubiKey PIV curve: {self.algorithm}"
        )

    def _find_private_key(self, session):
        """Find the private key object in the PIV slot (by CKA_ID)."""
        from pkcs11 import Attribute, ObjectClass

        keys = list(session.get_objects({
            Attribute.CLASS: ObjectClass.PRIVATE_KEY,
            Attribute.ID: self._slot_id(),
        }))
        if not keys:
            raise SigningBackendUnavailable(
                f"no private key in PIV slot {self._slot!r} — generate one first:\n"
                f"  ykman piv keys generate --algorithm ECCP256 {self._slot} /tmp/pub.pem"
            )
        return keys[0]

    # ── SigningBackend interface ───────────────────────────────────────────

    def available(self) -> bool:
        if self._lib_path is None or self._pin is None:
            return False
        try:
            with self._open_session() as session:
                self._find_cert(session)
                return True
        except SigningBackendUnavailable:
            return False

    def unavailable_reason(self) -> str:
        if self._lib_path is None:
            return (
                "yubikey-piv backend not available: libykcs11 not found — "
                "brew install yubico-piv-tool (macOS) or install ykcs11 (Linux), "
                "or set MSB_YUBIKEY_PKCS11_LIB"
            )
        if self._pin is None:
            return (
                f"yubikey-piv backend not available: no PIN — set MSB_YUBIKEY_PIN "
                f"or create {self._PIN_FILE}"
            )
        # Provisioned path: return "" when a session opens and a cert is
        # found (matches the base-class contract: available => no reason).
        try:
            with self._open_session() as session:
                self._find_cert(session)
            return ""
        except SigningBackendUnavailable as exc:
            return str(exc)

    def public_key_bytes(self) -> bytes:
        with self._open_session() as session:
            cert_obj = self._find_cert(session)
            return self._get_public_key_from_cert(session, cert_obj)

    def sign(self, message: bytes) -> bytes:
        """Sign *message* via PKCS#11 ECDSA-SHA256.

        Returns DER-encoded signature — the format ``verify_signature``
        expects.  No X9.62→DER conversion is needed.
        """
        from pkcs11 import Mechanism

        with self._open_session() as session:
            key = self._find_private_key(session)
            # ECDSA_SHA256 hashes internally and returns DER r||s.
            der_sig = key.sign(message, mechanism=Mechanism.ECDSA_SHA256)
        return bytes(der_sig)

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
