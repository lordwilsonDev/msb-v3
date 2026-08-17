"""RFC 3161 trusted timestamping (security-hardening #9) + receive-time fallback.

The anchor/notary timestamps are self-reported (``_now_iso()`` on the box the
attacker may own), so "when" is forgeable together with the chain. This module
gives the notary a *provable when* in two layers:

  * ``Rfc3161Timestamper`` — a real RFC 3161 Time-Stamp Authority client. The
    TSA signs a ``TimeStampToken`` whose ``messageImprint`` binds OUR content
    hash, and whose ``genTime`` is the authority's own clock — a third party
    attests both WHAT was stamped and WHEN. The token is validated
    cryptographically (messageImprint match, signed-attribute digest, signer
    certificate, signature, cert validity window) and the raw token is stored
    as evidence so an investigator can re-verify independently.
  * ``LocalReceiveTimestamper`` — the notary's independent receive-time: the
    local ``received_at`` of the notary process itself. No third party, no
    network — always available, and the meaningful fallback when the notary
    sink is genuinely off-box. It is explicitly NOT a TSA proof.

Fail-closed policy: ``Rfc3161Timestamper.stamp`` raises
``TimestampUnavailable`` (TSA unreachable / refused) or
``TimestampVerificationError`` (token present but does not cryptographically
cover our content). It never returns an unverified proof. Callers opt into the
weaker receive-time fallback explicitly (``allow_local_fallback=True``) — the
notary CLI only does so when ``MSB_TSA_ALLOW_LOCAL_FALLBACK=1``.

RFC 3161 wire details this module owns:

  * Request  — ``TimeStampReq`` (sha256, random nonce, ``certReq=FALSE``),
    sent as ``application/timestamp-query``.
  * Response — ``TimeStampResp`` (status granted) containing a ``ContentInfo``
    ``SignedData`` whose ``eContent`` is the DER of a ``TSTInfo``.
  * The signature is verified over the DER encoding of ``signedAttrs`` as a
    SET OF (RFC 5652 §5.4), which is what ``signed_attrs.dump()`` produces.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

import httpx
from asn1crypto import algos, cms, core, tsp
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa

# id-ct-TSTInfo (RFC 3161 §2.4.2): the content type of the signed eContent.
ID_CT_TSTINFO = "1.2.840.113549.1.9.16.1.4"
# id-aa-messageDigest / contentType / signingTime signed attributes.
ATTR_CONTENT_TYPE = "1.2.840.113549.1.9.3"
ATTR_MESSAGE_DIGEST = "1.2.840.113549.1.9.4"
ATTR_SIGNING_TIME = "1.2.840.113549.1.9.5"

TSA_TIMEOUT_ENV = "MSB_TSA_TIMEOUT"


class TimestampError(RuntimeError):
    """Base class for timestamping failures."""


class TimestampUnavailable(TimestampError):
    """The TSA could not be reached or refused to grant a timestamp."""


class TimestampVerificationError(TimestampError):
    """A TSA token was returned but does not cryptographically cover our content."""


@dataclass(frozen=True)
class TimestampProof:
    """A timestamped claim about ``content``, self-contained for audit storage.

    ``digest_sha256`` is the SHA-256 of the stamped content (notary entry
    bytes). For ``source="rfc3161"`` the ``token_b64`` holds the raw DER
    ``TimeStampToken`` — independently re-verifiable later — and ``gen_time``
    is the TSA's own signing time. For ``source="receive_time"`` only
    ``received_at`` is meaningful (the notary's own clock, no third party).
    """

    source: str  # "rfc3161" | "receive_time"
    digest_sha256: str
    tsa_url: Optional[str] = None
    gen_time: Optional[str] = None
    received_at: Optional[str] = None
    token_b64: Optional[str] = None
    verified: bool = False
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "TimestampProof":
        return cls(**{k: data[k] for k in cls.__dataclass_fields__ if k in data})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Timestamper(ABC):
    """A source of provable time for a piece of content."""

    @abstractmethod
    def stamp(self, content: bytes) -> TimestampProof: ...


class LocalReceiveTimestamper(Timestamper):
    """The notary's independent receive-time: this process's clock, always
    available, no network. Explicitly NOT a third-party proof — use it as the
    fallback or the offline default, never as a TSA substitute."""

    def stamp(self, content: bytes) -> TimestampProof:
        return TimestampProof(
            source="receive_time",
            digest_sha256=hashlib.sha256(content).hexdigest(),
            received_at=_now_iso(),
            verified=False,
            note="local receive time — not a third-party TSA (RFC 3161)",
        )


# ── RFC 3161 client ─────────────────────────────────────────────────────────


def build_timestamp_request(content_digest: bytes, nonce: int) -> bytes:
    """DER-encode an RFC 3161 ``TimeStampReq`` for a sha256 digest."""
    req = tsp.TimeStampReq(
        {
            "version": "v1",
            "message_imprint": tsp.MessageImprint(
                {
                    "hash_algorithm": algos.DigestAlgorithm({"algorithm": "sha256"}),
                    "hashed_message": content_digest,
                }
            ),
            "nonce": nonce,
            "cert_req": False,
        }
    )
    return req.dump()


def _signed_attr(signed_attrs: cms.CMSAttributes, attr_type: str) -> Optional[core.Asn1Value]:
    for attr in signed_attrs:
        if attr["type"].dotted == attr_type:
            return attr["values"][0]
    return None


def _select_signer_cert(signed_data: cms.SignedData, signer_info: cms.SignerInfo) -> Optional[cms.Certificate]:
    """Find the signer certificate matching the SignerInfo sid."""
    certificates = signed_data["certificates"]
    if not certificates:
        return None
    sid = signer_info["sid"]
    if sid.name == "issuer_and_serial_number":
        issuer = sid.chosen["issuer"]
        serial = sid.chosen["serial_number"].native
        for choice in certificates:
            cert = choice.chosen
            if cert["tbs_certificate"]["serial_number"].native == serial and (
                cert["tbs_certificate"]["issuer"].native == issuer.native
            ):
                return cert
        return None
    # subject_key_identifier sid: match against the cert's SKI extension.
    if sid.name == "subject_key_identifier":
        ski = sid.chosen.native
        for choice in certificates:
            cert = choice.chosen
            for ext in cert["tbs_certificate"]["extensions"]:
                if ext["extn_id"].native == "subject_key_identifier" and ext["extn_value"].native == ski:
                    return cert
        return None
    return None


def _verify_cms_signature(
    public_key_bytes: bytes,
    signature: bytes,
    signed_attrs_der: bytes,
    signature_algorithm: algos.SignedDigestAlgorithm,
) -> bool:
    """Verify the TSA's CMS signature over the DER-encoded signedAttrs."""
    public_key = serialization.load_der_public_key(public_key_bytes)
    sig_algo = signature_algorithm.signature_algo
    digest = hashes.SHA256()
    try:
        if sig_algo in ("rsassa_pkcs1v15", "rsassa_pss"):
            if not isinstance(public_key, rsa.RSAPublicKey):
                return False
            pad = (
                padding.PKCS1v15()
                if sig_algo == "rsassa_pkcs1v15"
                else padding.PSS(mgf=padding.MGF1(digest), salt_length=digest.digest_size)
            )
            public_key.verify(signature, signed_attrs_der, pad, digest)
            return True
        if sig_algo == "ecdsa":
            if not isinstance(public_key, ec.EllipticCurvePublicKey):
                return False
            public_key.verify(signature, signed_attrs_der, ec.ECDSA(hashes.SHA256()))
            return True
        if sig_algo == "ed25519":
            if not isinstance(public_key, ed25519.Ed25519PublicKey):
                return False
            public_key.verify(signature, signed_attrs_der)
            return True
    except (InvalidSignature, ValueError, TypeError):
        return False
    raise TimestampVerificationError(f"unsupported TSA signature algorithm: {sig_algo}")


class Rfc3161Timestamper(Timestamper):
    """RFC 3161 TSA client. Fail-closed: never returns an unverified proof.

    ``http_post(url, body, headers) -> (status_code, response_bytes)`` is
    injectable for tests; the default uses ``httpx`` (already a runtime dep).
    """

    def __init__(
        self,
        tsa_url: str,
        *,
        allow_local_fallback: bool = False,
        timeout: float = 10.0,
        http_post: Optional[Callable[[str, bytes, dict], tuple[int, bytes]]] = None,
    ) -> None:
        self.tsa_url = tsa_url
        self.allow_local_fallback = allow_local_fallback
        self.timeout = timeout
        self._http_post = http_post

    # -- transport -----------------------------------------------------------
    def _post(self, body: bytes) -> tuple[int, bytes]:
        if self._http_post is not None:
            try:
                return self._http_post(self.tsa_url, body, {"Content-Type": "application/timestamp-query", "Accept": "application/timestamp-reply"})
            except TimestampError:
                raise
            except Exception as exc:  # noqa: BLE001 — any transport failure is "unreachable"
                raise TimestampUnavailable(f"TSA unreachable ({self.tsa_url}): {exc}") from exc
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(
                    self.tsa_url,
                    content=body,
                    headers={"Content-Type": "application/timestamp-query", "Accept": "application/timestamp-reply"},
                )
                return response.status_code, response.content
        except httpx.HTTPError as exc:
            raise TimestampUnavailable(f"TSA unreachable ({self.tsa_url}): {exc}") from exc

    # -- the actual stamp ------------------------------------------------------
    def stamp(self, content: bytes) -> TimestampProof:
        digest = hashlib.sha256(content).digest()
        nonce = secrets.randbits(64)
        try:
            status, body = self._post(build_timestamp_request(digest, nonce))
        except TimestampError:
            if self.allow_local_fallback:
                proof = LocalReceiveTimestamper().stamp(content)
                return TimestampProof(
                    source=proof.source, digest_sha256=proof.digest_sha256, received_at=proof.received_at,
                    verified=False, note="TSA unavailable — receive-time fallback",
                )
            raise
        if status != 200 or not body:
            if self.allow_local_fallback:
                return TimestampProof(
                    source="receive_time", digest_sha256=hashlib.sha256(content).hexdigest(),
                    received_at=_now_iso(), verified=False,
                    note=f"TSA HTTP {status} — receive-time fallback",
                )
            raise TimestampUnavailable(f"TSA returned HTTP {status} (expected 200)")
        try:
            return self._validate(body, digest, nonce, content)
        except (TimestampVerificationError, TimestampUnavailable):
            if self.allow_local_fallback:
                return TimestampProof(
                    source="receive_time", digest_sha256=hashlib.sha256(content).hexdigest(),
                    received_at=_now_iso(), verified=False,
                    note="TSA token failed validation — receive-time fallback",
                )
            raise

    def _validate(self, response_bytes: bytes, digest: bytes, nonce: int, content: bytes) -> TimestampProof:
        try:
            response = tsp.TimeStampResp.load(response_bytes)
        except Exception as exc:  # noqa: BLE001 — any parse failure is a bad token
            raise TimestampVerificationError(f"response is not a valid RFC 3161 TimeStampResp: {exc}") from exc
        status = response["status"]["status"].native
        if status != "granted":
            raise TimestampUnavailable(f"TSA rejected the request (status={status!r})")
        token = response["time_stamp_token"]
        if token is None or token["content_type"].native != "signed_data":
            raise TimestampVerificationError("TimeStampToken is missing or not SignedData")

        signed_data = token["content"]
        encap = signed_data["encap_content_info"]
        if encap["content_type"].dotted != ID_CT_TSTINFO:
            raise TimestampVerificationError("signed content is not TSTInfo (id-ct-TSTInfo)")
        econtent = encap["content"]
        # The raw OCTET STRING contents (= DER TSTInfo). ``.native`` on a
        # ParsableOctetString returns a *parsed* inner value; ``.contents`` is
        # the exact stamped bytes, which is what messageDigest covers.
        econtent_bytes = econtent.contents
        tst_info = tsp.TSTInfo.load(econtent_bytes)

        # 1. The TSA must have stamped OUR digest.
        imprint = tst_info["message_imprint"]["hashed_message"].native
        if imprint != digest:
            raise TimestampVerificationError("TSA messageImprint does not match our content digest")
        # 2. Nonce echo closes request/response binding.
        if tst_info["nonce"].native != nonce:
            raise TimestampVerificationError("TSA nonce does not echo the request nonce")
        gen_time = tst_info["gen_time"].native  # UTC datetime (aware or naive)

        # 3. Signer certificate + signed attributes.
        signer_infos = signed_data["signer_infos"]
        if not signer_infos:
            raise TimestampVerificationError("TimeStampToken has no SignerInfo")
        signer_info = signer_infos[0]
        cert = _select_signer_cert(signed_data, signer_info)
        if cert is None:
            raise TimestampVerificationError("no signer certificate matches the SignerInfo sid")

        signed_attrs = signer_info["signed_attrs"]
        if signed_attrs is None:
            raise TimestampVerificationError("SignerInfo has no signedAttrs (bare signature)")
        # 4. contentType + messageDigest signed attributes over the eContent.
        md_attr = _signed_attr(signed_attrs, ATTR_MESSAGE_DIGEST)
        if md_attr is None or md_attr.native != hashlib.sha256(econtent_bytes).digest():
            raise TimestampVerificationError("signedAttrs messageDigest does not cover the TSTInfo content")
        ct_attr = _signed_attr(signed_attrs, ATTR_CONTENT_TYPE)
        if ct_attr is None or ct_attr.dotted != ID_CT_TSTINFO:
            raise TimestampVerificationError("signedAttrs contentType is not id-ct-TSTInfo")

        # 5. The signature over the DER-encoded signedAttrs. RFC 5652 §5.4:
        # the signature input is the SET OF form (tag 0x31), NOT the [0]
        # IMPLICIT form it has inside SignerInfo — asn1crypto's parsed
        # ``signed_attrs.dump()`` keeps the implicit tag, so re-wrap the
        # children as a fresh CMSAttributes to get the exact stamped bytes.
        signed_attrs_der = cms.CMSAttributes(list(signed_attrs)).dump()
        if not _verify_cms_signature(
            cert["tbs_certificate"]["subject_public_key_info"].dump(),
            signer_info["signature"].native,
            signed_attrs_der,
            signer_info["signature_algorithm"],
        ):
            raise TimestampVerificationError("TSA signature over signedAttrs is invalid")

        # 6. genTime must fall inside the signer certificate's validity window.
        try:
            crypto_cert = x509.load_der_x509_certificate(cert.dump())
            gen_naive = gen_time.replace(tzinfo=None) if gen_time.tzinfo else gen_time
            if gen_naive < crypto_cert.not_valid_before_utc.replace(tzinfo=None) or gen_naive > crypto_cert.not_valid_after_utc.replace(tzinfo=None):
                raise TimestampVerificationError("TSA genTime falls outside the signer certificate validity window")
        except TimestampVerificationError:
            raise
        except Exception as exc:  # noqa: BLE001 — unparseable cert is a failed validation
            raise TimestampVerificationError(f"signer certificate unparseable: {exc}") from exc

        return TimestampProof(
            source="rfc3161",
            digest_sha256=hashlib.sha256(content).hexdigest(),
            tsa_url=self.tsa_url,
            gen_time=gen_time.astimezone(timezone.utc).isoformat() if gen_time.tzinfo else gen_time.isoformat() + "+00:00",
            received_at=_now_iso(),
            token_b64=base64.b64encode(token.dump()).decode(),
            verified=True,
            note=f"RFC 3161 token validated (serial {tst_info['serial_number'].native})",
        )


def timestamper_from_env() -> Optional[Timestamper]:
    """Resolve the notary timestamper from env. None when no TSA is configured
    (receive-time only). ``MSB_TSA_URL`` enables RFC 3161; fail-closed unless
    ``MSB_TSA_ALLOW_LOCAL_FALLBACK=1``."""
    url = os.getenv("MSB_TSA_URL", "").strip()
    if not url:
        return None
    timeout = float(os.getenv(TSA_TIMEOUT_ENV, "10"))
    fallback = os.getenv("MSB_TSA_ALLOW_LOCAL_FALLBACK", "") == "1"
    return Rfc3161Timestamper(url, allow_local_fallback=fallback, timeout=timeout)
