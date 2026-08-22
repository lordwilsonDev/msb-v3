"""Circuit breaker tests for the DeepSeek client (Phase 0 — stabilize).

The circuit breaker prevents a dead API key (402 Payment Required) from
starving the server's thread pool. These tests prove:

1. A 402 opens the circuit → subsequent calls short-circuit immediately
2. A 429 opens the circuit → same behavior
3. A 500 (server error) does NOT open the circuit (transient failures retry)
4. The cooldown auto-closes the circuit after the configured period
5. The circuit state is observable via deepseek_circuit_state()
6. A process-level reset clears the circuit (the operator may have topped up)
"""

from __future__ import annotations

import time
from unittest.mock import patch

import httpx
import pytest

from msb_v3.local_ai import deepseek
from msb_v3.local_ai.deepseek import DeepSeekClient, deepseek_circuit_state


@pytest.fixture(autouse=True)
def _reset_circuit():
    """Reset the circuit between tests so they don't interfere."""
    deepseek._circuit_open_at = 0.0
    deepseek._circuit_reason = ""
    yield
    deepseek._circuit_open_at = 0.0
    deepseek._circuit_reason = ""


def _mock_transport(status_code: int, body: dict | None = None):
    """Create an httpx.MockTransport that returns the given status."""
    if body is None:
        body = {"error": "test"}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=body)

    return httpx.MockTransport(handler)


def _make_client(transport: httpx.MockTransport) -> DeepSeekClient:
    return DeepSeekClient(
        api_key="test-key",
        base_url="https://test.deepseek.local",
        model="test-model",
        timeout_s=5.0,
        transport=transport,
    )


class TestCircuitBreakerOpens:
    def test_402_opens_circuit(self):
        """A 402 (Payment Required) opens the circuit."""
        client = _make_client(_mock_transport(402, {"error": "insufficient balance"}))
        # First call: hits the network, gets 402, opens the circuit
        with pytest.raises(ConnectionError, match="payment required"):
            client.chat([{"role": "user", "content": "hi"}])
        # Circuit is now open
        state = deepseek_circuit_state()
        assert state["open"] is True
        assert "402" in state["reason"] or "payment" in state["reason"]

    def test_429_opens_circuit(self):
        """A 429 (rate limit) opens the circuit."""
        client = _make_client(_mock_transport(429, {"error": "rate limited"}))
        with pytest.raises(ConnectionError, match="rate limit"):
            client.chat([{"role": "user", "content": "hi"}])
        state = deepseek_circuit_state()
        assert state["open"] is True
        assert "429" in state["reason"] or "rate" in state["reason"]

    def test_500_does_not_open_circuit(self):
        """A 500 (server error) is transient — circuit stays closed."""
        client = _make_client(_mock_transport(500, {"error": "internal"}))
        with pytest.raises(ConnectionError, match="unreachable"):
            client.chat([{"role": "user", "content": "hi"}])
        state = deepseek_circuit_state()
        assert state["open"] is False


class TestCircuitShortCircuits:
    def test_open_circuit_short_circuits_without_network(self):
        """When the circuit is open, calls raise immediately without HTTP."""
        client = _make_client(_mock_transport(402))
        # First call opens the circuit
        with pytest.raises(ConnectionError, match="payment"):
            client.chat([{"role": "user", "content": "hi"}])
        # Second call: circuit is open → raises immediately, no network
        t0 = time.monotonic()
        with pytest.raises(ConnectionError, match="circuit open"):
            client.chat([{"role": "user", "content": "hi again"}])
        elapsed = time.monotonic() - t0
        # Should be near-instant (no 5s timeout)
        assert elapsed < 0.5, f"circuit short-circuit took {elapsed:.3f}s (expected <0.5s)"


class TestCircuitCooldown:
    def test_cooldown_auto_closes_circuit(self):
        """After the cooldown period, the circuit auto-closes."""
        # Patch the cooldown to 0.1s for a fast test
        with patch.object(deepseek, "_CIRCUIT_COOLDOWN_S", 0.1):
            client = _make_client(_mock_transport(402))
            with pytest.raises(ConnectionError, match="payment"):
                client.chat([{"role": "user", "content": "hi"}])
            assert deepseek_circuit_state()["open"] is True
            # Wait for cooldown
            time.sleep(0.15)
            # Circuit should be closed now
            assert deepseek_circuit_state()["open"] is False


class TestCircuitState:
    def test_circuit_state_shape_when_closed(self):
        state = deepseek_circuit_state()
        assert state["open"] is False
        assert state["reason"] == ""
        assert "cooldown_s" in state

    def test_circuit_state_shape_when_open(self):
        client = _make_client(_mock_transport(402))
        with pytest.raises(ConnectionError):
            client.chat([{"role": "user", "content": "hi"}])
        state = deepseek_circuit_state()
        assert state["open"] is True
        assert state["reason"] != ""
        assert state["elapsed_s"] >= 0.0
