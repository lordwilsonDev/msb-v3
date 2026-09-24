"""Pinned, fail-closed JSON-RPC client for the Aeon Orb MCP endpoint."""

from __future__ import annotations

import hashlib
import hmac
import http.client
import itertools
import json
import re
import socket
import ssl
from typing import Any

from msb_v3.speech.realtime.aeon_config import AeonConfig

_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_TOOL_TIMEOUTS = {
    "state": 3.0,
    "screen_find": 3.0,
    "click_at": 3.0,
    "move_pointer": 3.0,
    "click": 3.0,
    "scroll": 3.0,
    "describe_screen": 20.0,
    "release_all": 1.0,
}
_DEFAULT_TIMEOUT = 5.0
_DEFAULT_MAX_RESPONSE_BYTES = 64 * 1024
_MAX_REQUEST_BYTES = 64 * 1024


class AeonClientError(RuntimeError):
    """A safe, user-facing Orb transport or protocol failure."""


class AeonClient:
    """Call the Orb without proxies, redirects, retries, or secret-bearing errors."""

    def __init__(
        self,
        config: AeonConfig,
        *,
        max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        if isinstance(max_response_bytes, bool) or not 1 <= max_response_bytes <= 1024 * 1024:
            raise ValueError("max_response_bytes must be from 1 to 1048576")
        self.config = config
        self.max_response_bytes = max_response_bytes
        self._ids = itertools.count(1)

    def initialize(self, *, timeout: float = _DEFAULT_TIMEOUT) -> dict[str, Any]:
        """Perform the MCP initialize handshake and return its bounded result."""
        return self._request("initialize", {}, timeout=timeout)

    def call(
        self,
        tool: str,
        arguments: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Invoke one Orb MCP tool and return its bounded JSON object result."""
        if not _TOOL_NAME_RE.fullmatch(tool):
            raise AeonClientError("invalid Orb tool name")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise AeonClientError("Orb tool arguments must be an object")
        effective_timeout = _validate_timeout(timeout if timeout is not None else _TOOL_TIMEOUTS.get(tool, _DEFAULT_TIMEOUT))
        result = self._request("tools/call", {"name": tool, "arguments": arguments}, timeout=effective_timeout)
        return _decode_tool_result(tool, result)

    def _request(self, method: str, params: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        request_id = next(self._ids)
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        try:
            body = json.dumps(
                payload,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError):
            raise AeonClientError("Orb request is not JSON serializable") from None
        if len(body) > _MAX_REQUEST_BYTES:
            raise AeonClientError("Orb request is too large")

        try:
            status, response_body = self._post(body, timeout=timeout)
        except AeonClientError:
            raise
        except (TimeoutError, socket.timeout):
            raise AeonClientError(f"{method} timed out") from None
        except ssl.SSLError as exc:
            raise AeonClientError(f"{method} TLS failure ({type(exc).__name__})") from None
        except (OSError, http.client.HTTPException) as exc:
            raise AeonClientError(f"{method} transport failure ({type(exc).__name__})") from None

        if 300 <= status < 400:
            raise AeonClientError("Orb redirect refused")
        if status in {401, 403}:
            raise AeonClientError(f"Orb authentication rejected ({status})")
        if status != 200:
            raise AeonClientError(f"Orb returned HTTP {status}")

        try:
            envelope = json.loads(response_body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise AeonClientError("Orb returned malformed JSON") from None
        if not isinstance(envelope, dict) or envelope.get("jsonrpc") != "2.0":
            raise AeonClientError("Orb returned an invalid JSON-RPC envelope")
        if envelope.get("id") != request_id:
            raise AeonClientError("Orb JSON-RPC response id mismatch")
        if "error" in envelope:
            error = envelope["error"]
            code = error.get("code") if isinstance(error, dict) else None
            suffix = f" ({code})" if isinstance(code, (str, int)) else ""
            raise AeonClientError(f"Orb JSON-RPC error{suffix}")
        result = envelope.get("result")
        if not isinstance(result, dict):
            raise AeonClientError("Orb JSON-RPC result is not an object")
        return result

    def _post(self, body: bytes, *, timeout: float) -> tuple[int, bytes]:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        raw_socket = socket.create_connection((self.config.host, self.config.port), timeout=timeout)
        try:
            with context.wrap_socket(raw_socket, server_hostname=self.config.host) as tls_socket:
                _verify_fingerprint(tls_socket, self.config.cert_sha256)
                request = _http_request(
                    authority=self.config.authority,
                    token=self.config.token,
                    body=body,
                )
                tls_socket.sendall(request)
                response = http.client.HTTPResponse(tls_socket, method="POST")
                response.begin()
                status = response.status
                if status is None:
                    raise AeonClientError("Orb returned no HTTP status")
                if not 300 <= status < 400:
                    response_body = response.read(self.max_response_bytes + 1)
                    if len(response_body) > self.max_response_bytes:
                        raise AeonClientError("Orb response is too large")
                    return status, response_body
                return status, b""
        finally:
            raw_socket.close()


def _verify_fingerprint(tls_socket: ssl.SSLSocket, expected: str) -> None:
    certificate = tls_socket.getpeercert(binary_form=True)
    if not certificate:
        raise AeonClientError("Orb presented no certificate")
    actual = hashlib.sha256(certificate).hexdigest().upper()
    if not hmac.compare_digest(actual, expected):
        raise AeonClientError("Orb certificate fingerprint mismatch")


def _http_request(*, authority: str, token: str, body: bytes) -> bytes:
    headers = (
        "POST /api/mcp HTTP/1.1\r\n"
        f"Host: {authority}\r\n"
        "Authorization: Bearer " + token + "\r\n"
        "Content-Type: application/json\r\n"
        "Accept: application/json\r\n"
        "User-Agent: msb-v3-aeon/1\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n"
        "\r\n"
    ).encode("ascii")
    return headers + body


def _decode_tool_result(tool: str, result: dict[str, Any]) -> dict[str, Any]:
    if result.get("isError") is True:
        raise AeonClientError(f"Orb tool {tool} reported an error")
    content = result.get("content")
    if not isinstance(content, list):
        raise AeonClientError(f"Orb tool {tool} returned invalid content")
    texts = [item.get("text") for item in content if isinstance(item, dict) and item.get("type") == "text"]
    if not texts or not isinstance(texts[0], str):
        raise AeonClientError(f"Orb tool {tool} returned no text result")
    try:
        decoded = json.loads(texts[0])
    except json.JSONDecodeError:
        return {"text": texts[0]}
    if not isinstance(decoded, dict):
        raise AeonClientError(f"Orb tool {tool} result is not a JSON object")
    return decoded


def _validate_timeout(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= 120:
        raise AeonClientError("Orb timeout must be from 0 to 120 seconds")
    return float(value)
