from __future__ import annotations

import ipaddress
import json
import ssl
import threading
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from msb_v3.speech.realtime.aeon_client import AeonClient, AeonClientError
from msb_v3.speech.realtime.aeon_config import AeonConfig

TOKEN = "aeon_tok_failure_injection_secret"


class _Handler(BaseHTTPRequestHandler):
    records: list[dict[str, object]] = []

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {}
        name = payload.get("params", {}).get("name", "")
        self.records.append({"path": self.path, "headers": dict(self.headers), "payload": payload})

        if name == "redirect":
            self.send_response(302)
            self.send_header("Location", "/elsewhere")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if name == "unauthorized":
            body = f"token={TOKEN}".encode()
            self._send(401, body)
            return
        if name == "oversized":
            self._send(200, b"x" * 512)
            return
        if name == "slow":
            time.sleep(0.2)
        if name == "malformed":
            self._send(200, b"{")
            return
        if name == "rpc_error":
            self._send_json({"jsonrpc": "2.0", "id": payload.get("id"),
                             "error": {"code": -32000, "message": TOKEN}})
            return
        if name == "wrong_id":
            self._send_json({"jsonrpc": "2.0", "id": payload.get("id", 0) + 1, "result": {}})
            return
        if name == "is_error":
            self._send_json({"jsonrpc": "2.0", "id": payload.get("id"), "result": {
                "content": [{"type": "text", "text": TOKEN}], "isError": True}})
            return

        result = {"protocolVersion": "2025-06-18", "serverInfo": {"name": "test-orb"}}
        if name:
            result = {"content": [{"type": "text", "text": json.dumps({
                "ok": True, "persona": "generic-absolute", "name": name})}]}
        self._send_json({"jsonrpc": "2.0", "id": payload.get("id"), "result": result})

    def _send_json(self, payload: object) -> None:
        self._send(200, json.dumps(payload).encode())

    def _send(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *args: object) -> None:
        return


@pytest.fixture
def tls_orb(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([
            x509.DNSName("localhost"),
            x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
        ]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / "orb.crt"
    key_path = tmp_path / "orb.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ))
    key_path.chmod(0o600)

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    _Handler.records = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.daemon_threads = True
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield SimpleNamespace(
            host="127.0.0.1",
            port=server.server_address[1],
            fingerprint=cert.fingerprint(hashes.SHA256()).hex(),
            records=_Handler.records,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _config(tls_orb, *, fingerprint: str | None = None) -> AeonConfig:
    return AeonConfig(
        host=tls_orb.host,
        port=tls_orb.port,
        token=TOKEN,
        cert_sha256=fingerprint or tls_orb.fingerprint,
    )


def test_initialize_and_state_round_trip(tls_orb):
    client = AeonClient(_config(tls_orb))
    initialized = client.initialize()
    state = client.call("state")

    assert initialized["serverInfo"]["name"] == "test-orb"
    assert state == {"ok": True, "persona": "generic-absolute", "name": "state"}
    assert len(tls_orb.records) == 2
    assert all(record["path"] == "/api/mcp" for record in tls_orb.records)
    assert all(record["headers"]["Authorization"] == f"Bearer {TOKEN}" for record in tls_orb.records)


def test_client_ignores_proxy_environment_and_connects_directly(tls_orb, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:1")
    client = AeonClient(_config(tls_orb))
    assert client.call("state")["ok"] is True
    assert len(tls_orb.records) == 1


def test_wrong_fingerprint_fails_before_authenticated_http_request(tls_orb):
    client = AeonClient(_config(tls_orb, fingerprint="00" * 32))
    with pytest.raises(AeonClientError, match="fingerprint mismatch"):
        client.initialize()
    assert tls_orb.records == []


def test_redirect_is_refused_and_never_followed(tls_orb):
    client = AeonClient(_config(tls_orb))
    with pytest.raises(AeonClientError, match="redirect refused"):
        client.call("redirect")
    assert [record["path"] for record in tls_orb.records] == ["/api/mcp"]


def test_authentication_error_redacts_token_and_response_body(tls_orb):
    client = AeonClient(_config(tls_orb))
    with pytest.raises(AeonClientError) as exc:
        client.call("unauthorized")
    message = str(exc.value)
    assert "401" in message
    assert TOKEN not in message
    assert len(tls_orb.records) == 1


def test_timeout_is_bounded_and_not_retried(tls_orb):
    client = AeonClient(_config(tls_orb))
    with pytest.raises(AeonClientError, match="timed out"):
        client.call("slow", timeout=0.03)
    assert len(tls_orb.records) == 1


def test_malformed_json_is_rejected(tls_orb):
    client = AeonClient(_config(tls_orb))
    with pytest.raises(AeonClientError, match="malformed JSON"):
        client.call("malformed")


def test_oversized_response_is_rejected(tls_orb):
    client = AeonClient(_config(tls_orb), max_response_bytes=128)
    with pytest.raises(AeonClientError, match="response is too large"):
        client.call("oversized")


def test_jsonrpc_error_message_never_reaches_exception(tls_orb):
    client = AeonClient(_config(tls_orb))
    with pytest.raises(AeonClientError) as exc:
        client.call("rpc_error")
    assert "-32000" in str(exc.value)
    assert TOKEN not in str(exc.value)


def test_response_id_mismatch_is_rejected(tls_orb):
    client = AeonClient(_config(tls_orb))
    with pytest.raises(AeonClientError, match="id mismatch"):
        client.call("wrong_id")


def test_tool_error_is_rejected_without_exposing_text(tls_orb):
    client = AeonClient(_config(tls_orb))
    with pytest.raises(AeonClientError) as exc:
        client.call("is_error")
    assert "reported an error" in str(exc.value)
    assert TOKEN not in str(exc.value)


@pytest.mark.parametrize(
    ("tool", "arguments", "message"),
    [
        ("bad name", {}, "invalid Orb tool name"),
        ("state", [], "arguments must be an object"),
    ],
)
def test_invalid_tool_inputs_fail_before_network(tls_orb, tool, arguments, message):
    client = AeonClient(_config(tls_orb))
    with pytest.raises(AeonClientError, match=message):
        client.call(tool, arguments)
    assert tls_orb.records == []


def test_non_standard_json_numbers_fail_before_network(tls_orb):
    client = AeonClient(_config(tls_orb))
    with pytest.raises(AeonClientError, match="not JSON serializable"):
        client.call("state", {"value": float("nan")})
    assert tls_orb.records == []


def test_oversized_request_fails_before_network(tls_orb):
    client = AeonClient(_config(tls_orb))
    with pytest.raises(AeonClientError, match="request is too large"):
        client.call("state", {"text": "x" * (64 * 1024)})
    assert tls_orb.records == []


def test_invalid_timeout_fails_before_network(tls_orb):
    client = AeonClient(_config(tls_orb))
    with pytest.raises(AeonClientError, match="timeout"):
        client.call("state", timeout=0)
    assert tls_orb.records == []
