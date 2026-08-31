"""Unit tests for the h08 fault-injection proxy (h08_chaos_proxy.py).

Each fault class is proven in isolation against a FAKE upstream HTTP server
(no real msb-v3 needed):

  none       transparent relay — full response reaches the client verbatim
  latency    the injected delay actually delays the first response bytes
  drop       connection closed without relaying any response bytes
  truncate   only the first N bytes relayed, then the connection closes
             (a body cut mid-stream, never a silent false-200)

These mirror exactly what the h08 chaos runner asserts against the live
server, but deterministically and without external dependencies.
"""

from __future__ import annotations

import asyncio
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

# Every test here spawns the fault-injection proxy as a subprocess — timing
# sensitive, gated out of the hermetic release core (PRODUCTION-CLOSURE-001 P1).
pytestmark = pytest.mark.chaos

PROXY = Path(__file__).resolve().parents[2] / "scripts" / "hygiene" / "h08_chaos_proxy.py"

BIG_BODY = "X" * 4096
RESPONSE = (
    "HTTP/1.1 200 OK\r\n"
    "Content-Type: text/plain\r\n"
    f"Content-Length: {len(BIG_BODY)}\r\n"
    "\r\n"
    + BIG_BODY
)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class FakeUpstream:
    """Tiny asyncio HTTP server that answers every request with RESPONSE."""

    def __init__(self) -> None:
        self.port = _free_port()
        self._shutdown = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        # Wait until the server socket is accepting.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.5):
                    return
            except OSError:
                time.sleep(0.05)
        raise RuntimeError("fake upstream failed to start")

    def _run(self) -> None:
        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await reader.read(65536)  # consume the request
            writer.write(RESPONSE.encode("utf-8"))
            await writer.drain()
            writer.close()

        async def serve() -> None:
            server = await asyncio.start_server(handle, "127.0.0.1", self.port)
            async with server:
                # Serve until stop() is called; then exit cleanly so the loop
                # is not left mid-future (avoids loop-stop warnings).
                while not self._shutdown.is_set():
                    await asyncio.sleep(0.05)

        asyncio.run(serve())

    def stop(self) -> None:
        self._shutdown.set()
        if self._thread:
            self._thread.join(timeout=5)


@pytest.fixture()
def upstream() -> FakeUpstream:
    srv = FakeUpstream()
    srv.start()
    yield srv
    srv.stop()


def _start_proxy(fault: str, port: int, upstream_port: int, **kw) -> subprocess.Popen:
    cmd = [
        sys.executable, str(PROXY),
        "--port", str(port),
        "--upstream-port", str(upstream_port),
        "--fault", fault,
    ]
    if fault == "latency" and kw.get("ms"):
        cmd += ["--ms", str(kw["ms"])]
    if fault == "truncate" and kw.get("truncate_bytes"):
        cmd += ["--truncate-bytes", str(kw["truncate_bytes"])]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def _wait_port(
    port: int, timeout_s: float = 30.0, proc: subprocess.Popen | None = None
) -> None:
    """Block until ``port`` accepts a connection.

    ``timeout_s`` is generous (30s): CI hosts that also run the live stack can
    take several seconds just to fork a fresh Python interpreter, and a tight
    budget here was the dominant ``release-verify`` flake. If ``proc`` is
    given and the child has already exited, fail immediately with its captured
    output instead of waiting out the whole deadline.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            out = ""
            try:
                out = (proc.stdout.read() if proc.stdout else "") or ""
            except Exception:
                pass
            raise RuntimeError(
                f"proxy subprocess exited early (rc={proc.returncode}) before "
                f":{port} came up\n--- child output ---\n{out}"
            )
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(f"proxy on :{port} never came up within {timeout_s:.0f}s")


def _proxy_request(port: int, timeout_s: float = 5.0) -> bytes:
    """Send one GET through the proxy; return everything received (or b'')."""
    with socket.create_connection(("127.0.0.1", port), timeout=timeout_s) as sock:
        sock.sendall(b"GET /health HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        sock.settimeout(timeout_s)
        chunks: list[bytes] = []
        while True:
            try:
                data = sock.recv(65536)
            except (ConnectionResetError, socket.timeout):
                break
            if not data:
                break
            chunks.append(data)
    return b"".join(chunks)


def _run_proxy_scenario(fault: str, upstream: FakeUpstream, **kw) -> tuple[bytes, subprocess.Popen]:
    port = _free_port()
    proc = _start_proxy(fault, port, upstream.port, **kw)
    try:
        _wait_port(port, proc=proc)
        data = _proxy_request(port)
    finally:
        proc.kill()
        proc.wait(timeout=10)
    return data, proc


def test_none_relays_response_verbatim(upstream: FakeUpstream) -> None:
    data, _ = _run_proxy_scenario("none", upstream)
    assert data.startswith(b"HTTP/1.1 200 OK")
    assert data.endswith(BIG_BODY.encode("utf-8"))
    assert len(data) == len(RESPONSE.encode("utf-8"))


def test_latency_delays_response(upstream: FakeUpstream) -> None:
    delay_ms = 400
    port = _free_port()
    proc = _start_proxy("latency", port, upstream.port, ms=delay_ms)
    try:
        _wait_port(port, proc=proc)
        started = time.monotonic()
        data = _proxy_request(port, timeout_s=5.0)
        elapsed_ms = (time.monotonic() - started) * 1000
    finally:
        proc.kill()
        proc.wait(timeout=10)
    # The full response still arrives (graceful degradation, not failure)…
    assert data.startswith(b"HTTP/1.1 200 OK")
    # …but only after the injected delay.
    assert elapsed_ms >= delay_ms * 0.8, f"elapsed={elapsed_ms:.0f}ms < 80% of {delay_ms}ms"


def test_drop_closes_without_relaying(upstream: FakeUpstream) -> None:
    data, _ = _run_proxy_scenario("drop", upstream)
    assert data == b"", "drop must close the client connection without any response bytes"


def test_truncate_cuts_response_mid_body(upstream: FakeUpstream) -> None:
    cut = 64
    data, _ = _run_proxy_scenario("truncate", upstream, truncate_bytes=cut)
    # Exactly `cut` bytes (or fewer on a reset) — the body never completes.
    assert len(data) <= cut
    assert BIG_BODY.encode("utf-8") not in data
    # The full response must NOT appear; a truncated reply is never a 200.
    assert len(data) < len(RESPONSE.encode("utf-8"))


def test_truncate_of_small_response_is_observable(upstream: FakeUpstream) -> None:
    """A cut that lands inside the response must still be observable.

    Guards the regression the chaos runner hit live: /health responses are
    small enough that a large truncate window passes them whole. Here we cut
    at 64 bytes against the 4 KiB response — guaranteed to land mid-body.
    """
    cut = 64
    port = _free_port()
    proc = _start_proxy("truncate", port, upstream.port, truncate_bytes=cut)
    try:
        _wait_port(port, proc=proc)
        with socket.create_connection(("127.0.0.1", port), timeout=5.0) as sock:
            sock.sendall(b"GET /health HTTP/1.1\r\nHost: localhost\r\n\r\n")
            sock.settimeout(5.0)
            first = sock.recv(65536)
    finally:
        proc.kill()
        proc.wait(timeout=10)
    assert len(first) <= cut
    assert BIG_BODY.encode("utf-8") not in first
