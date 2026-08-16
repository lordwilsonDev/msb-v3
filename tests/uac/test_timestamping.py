"""RFC 3161 trusted timestamping (security-hardening #9).

The happy path is tested against a CRYPTOGRAPHICALLY REAL synthetic TSA: the
test builds a TimeStampToken exactly the way a TSA would (self-signed cert,
signedAttrs with contentType/messageDigest/signingTime, RSA signature over the
SET-OF signedAttrs per RFC 5652 §5.4) and the client must validate it — proof
the whole verification path works, not just the plumbing. Fail-closed paths
(imprint mismatch, bad signature, TSA rejection, unreachable TSA) are tested
with the same builder.
"""

from __future__ import annotations

import datetime
import hashlib
import json

import pytest
from asn1crypto import algos, cms, tsp
from asn1crypto import x509 as asn1_x509
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

from msb_v3.uac.timestamping import (
    ID_CT_TSTINFO,
    LocalReceiveTimestamper,
    Rfc3161Timestamper,
    TimestampProof,
    TimestampUnavailable,
    TimestampVerificationError,
    build_timestamp_request,
)


class FakeTSA:
    """Builds and serves RFC 3161 responses signed by a real RSA key."""

    def __init__(
        self,
        gen_time: datetime.datetime | None = None,
        fixed_digest: bytes | None = None,
    ) -> None:
        """``fixed_digest`` simulates a malicious/misconfigured TSA: it stamps
        that digest regardless of the request instead of echoing the client's."""
        self.fixed_digest = fixed_digest
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "msb-test-tsa")])
        self.gen_time = gen_time or datetime.datetime(2026, 8, 16, 7, 10, 5, tzinfo=datetime.timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(self.key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc))
            .not_valid_after(datetime.datetime(2030, 1, 1, tzinfo=datetime.timezone.utc))
            .sign(self.key, hashes.SHA256())
        )
        self.cert = asn1_x509.Certificate.load(cert.public_bytes(serialization.Encoding.DER))

    def serve(self, request_bytes: bytes) -> bytes:
        """Satisfy an RFC 3161 request with a properly signed token."""
        request = tsp.TimeStampReq.load(request_bytes)
        digest = self.fixed_digest or request["message_imprint"]["hashed_message"].native
        nonce = request["nonce"].native

        tst = tsp.TSTInfo(
            {
                "version": "v1",
                "policy": "1.3.6.1.4.1.99999.1",
                "message_imprint": tsp.MessageImprint(
                    {
                        "hash_algorithm": algos.DigestAlgorithm({"algorithm": "sha256"}),
                        "hashed_message": digest,
                    }
                ),
                "serial_number": 4242,
                "gen_time": self.gen_time,
                "nonce": nonce,
            }
        )
        econtent = tst.dump()
        attrs = cms.CMSAttributes(
            [
                cms.CMSAttribute({"type": "content_type", "values": [ID_CT_TSTINFO]}),
                cms.CMSAttribute({"type": "message_digest", "values": [hashlib.sha256(econtent).digest()]}),
                cms.CMSAttribute({"type": "signing_time", "values": [cms.Time(name="utc_time", value=self.gen_time)]}),
            ]
        )
        signature = self.key.sign(attrs.dump(), padding.PKCS1v15(), hashes.SHA256())
        signer = cms.SignerInfo(
            {
                "version": "v1",
                "sid": cms.SignerIdentifier(
                    {
                        "issuer_and_serial_number": cms.IssuerAndSerialNumber(
                            {
                                "issuer": self.cert["tbs_certificate"]["issuer"],
                                "serial_number": self.cert["tbs_certificate"]["serial_number"],
                            }
                        )
                    }
                ),
                "digest_algorithm": algos.DigestAlgorithm({"algorithm": "sha256"}),
                "signed_attrs": attrs,
                "signature_algorithm": algos.SignedDigestAlgorithm({"algorithm": "sha256_rsa"}),
                "signature": signature,
            }
        )
        signed_data = cms.SignedData(
            {
                "version": "v3",
                "digest_algorithms": cms.DigestAlgorithms([algos.DigestAlgorithm({"algorithm": "sha256"})]),
                "encap_content_info": cms.EncapsulatedContentInfo(
                    {"content_type": ID_CT_TSTINFO, "content": cms.ParsableOctetString(econtent)}
                ),
                "certificates": cms.CertificateSet([self.cert]),
                "signer_infos": cms.SignerInfos([signer]),
            }
        )
        response = tsp.TimeStampResp(
            {
                "status": tsp.PKIStatusInfo({"status": "granted"}),
                "time_stamp_token": cms.ContentInfo({"content_type": "signed_data", "content": signed_data}),
            }
        )
        return response.dump()


def _poster(tsa: FakeTSA):
    def post(url: str, body: bytes, headers: dict) -> tuple[int, bytes]:
        assert url == "https://tsa.invalid"
        assert headers["Content-Type"] == "application/timestamp-query"
        return 200, tsa.serve(body)

    return post


def test_build_timestamp_request_encodes_digest_and_nonce():
    digest = hashlib.sha256(b"entry").digest()
    nonce = 987654321
    request = tsp.TimeStampReq.load(build_timestamp_request(digest, nonce))
    assert request["message_imprint"]["hash_algorithm"]["algorithm"].native == "sha256"
    assert request["message_imprint"]["hashed_message"].native == digest
    assert request["nonce"].native == nonce


def test_local_receive_timestamper_is_offline_and_unverified():
    proof = LocalReceiveTimestamper().stamp(b"some entry bytes")
    assert proof.source == "receive_time"
    assert proof.verified is False
    assert proof.digest_sha256 == hashlib.sha256(b"some entry bytes").hexdigest()
    assert proof.received_at is not None
    assert proof.token_b64 is None


def test_rfc3161_roundtrip_validates_a_real_token():
    tsa = FakeTSA()
    content = b"the exact notarized entry bytes"
    timestamper = Rfc3161Timestamper("https://tsa.invalid", http_post=_poster(tsa))
    proof = timestamper.stamp(content)
    assert proof.source == "rfc3161"
    assert proof.verified is True
    assert proof.gen_time == "2026-08-16T07:10:05+00:00"
    assert proof.digest_sha256 == hashlib.sha256(content).hexdigest()
    assert proof.tsa_url == "https://tsa.invalid"
    assert proof.token_b64  # raw DER token preserved for independent re-verification
    # The stored token must parse and still cover the same digest.
    token = cms.ContentInfo.load(__import__("base64").b64decode(proof.token_b64))
    tst = tsp.TSTInfo.load(token["content"]["encap_content_info"]["content"].contents)
    assert tst["message_imprint"]["hashed_message"].native == hashlib.sha256(content).digest()


def test_rfc3161_rejects_a_different_message_imprint():
    # The TSA stamps a digest that does NOT match the content we sent.
    tsa = FakeTSA(fixed_digest=hashlib.sha256(b"something else").digest())
    timestamper = Rfc3161Timestamper("https://tsa.invalid", http_post=_poster(tsa))
    with pytest.raises(TimestampVerificationError, match="messageImprint"):
        timestamper.stamp(b"content that was NOT stamped")


def test_rfc3161_rejects_a_tampered_signature():
    tsa = FakeTSA()

    def tampering_post(url, body, headers):
        _, resp = _poster(tsa)(url, body, headers)
        # Flip a byte at the END of the CMS token — the signature is the last
        # field of the SignedData, so parsing stays intact (imprint/nonce still
        # match) but the signature must no longer verify.
        response = tsp.TimeStampResp.load(resp)
        token_der = response["time_stamp_token"].dump()
        tampered = token_der[:-5] + bytes([token_der[-5] ^ 0xFF]) + token_der[-4:]
        response["time_stamp_token"] = cms.ContentInfo.load(tampered)
        return 200, response.dump()

    timestamper = Rfc3161Timestamper("https://tsa.invalid", http_post=tampering_post)
    with pytest.raises(TimestampVerificationError, match="signature"):
        timestamper.stamp(b"content")


def test_rfc3161_fails_closed_on_rejection():
    def reject(url, body, headers):
        resp = tsp.TimeStampResp(
            {
                "status": tsp.PKIStatusInfo({"status": "rejection"}),
                # asn1crypto's TimeStampResp requires the token field; a real
                # TSA omits it on rejection, but the client must fail on the
                # status alone before ever looking at the token.
                "time_stamp_token": cms.ContentInfo({"content_type": "data"}),
            }
        )
        return 200, resp.dump()

    timestamper = Rfc3161Timestamper("https://tsa.invalid", http_post=reject)
    with pytest.raises(TimestampUnavailable, match="rejected"):
        timestamper.stamp(b"content")


def test_rfc3161_fails_closed_on_network_failure():
    def offline(url, body, headers):
        raise RuntimeError("connection refused")

    timestamper = Rfc3161Timestamper("https://tsa.invalid", http_post=offline)
    with pytest.raises(TimestampUnavailable, match="unreachable"):
        timestamper.stamp(b"content")
    # Fallback is opt-in and explicitly weaker.
    fallback = Rfc3161Timestamper("https://tsa.invalid", http_post=offline, allow_local_fallback=True)
    proof = fallback.stamp(b"content")
    assert proof.source == "receive_time"
    assert proof.verified is False
    assert "fallback" in proof.note


def test_timestamp_proof_dict_roundtrip():
    proof = TimestampProof(
        source="rfc3161",
        digest_sha256="ab" * 32,
        tsa_url="https://tsa.invalid",
        gen_time="2026-08-16T07:10:05+00:00",
        received_at="2026-08-16T07:10:06+00:00",
        token_b64="dG9rZW4=",
        verified=True,
        note="ok",
    )
    restored = TimestampProof.from_dict(json.loads(json.dumps(proof.to_dict())))
    assert restored == proof
