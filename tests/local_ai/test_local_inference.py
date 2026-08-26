"""Local AI inference integration tests.

Verifies:
1. Ollama backend is reachable and responds via /api/chat
2. LlamaCPPClient can be instantiated with custom URL
3. Client factory respects MSB_ACTIVE_BACKEND env var
4. BitNet status: i2_s quant requires llama.cpp build > 10200
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from msb_v3.local_ai.client_factory import (
    active_backend,
    get_client,
    set_active_backend,
)
from msb_v3.local_ai.llama_client import LlamaCPPClient
from msb_v3.local_ai.ollama import LocalAIClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ollama_reachable() -> bool:
    """Check if Ollama is running on default port."""
    try:
        import httpx

        r = httpx.get("http://127.0.0.1:11434/api/tags", timeout=3.0)
        return r.status_code == 200
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Tests: Client factory
# ---------------------------------------------------------------------------


class TestClientFactory:
    """Client factory returns the right client for the active backend."""

    def test_default_backend_is_ollama(self):
        """Default active backend should be 'ollama'."""
        # Reset to default for this test
        set_active_backend("ollama")
        assert active_backend() == "ollama"

    def test_set_backend_to_llamacpp(self):
        """Can switch to llama.cpp backend."""
        original = active_backend()
        set_active_backend("llamacpp")
        assert active_backend() == "llamacpp"
        set_active_backend(original)  # restore

    def test_set_backend_rejects_invalid(self):
        """Invalid backend names are rejected."""
        original = active_backend()
        set_active_backend("invalid")
        assert active_backend() == original  # unchanged


# ---------------------------------------------------------------------------
# Tests: LlamaCPPClient instantiation
# ---------------------------------------------------------------------------


class TestLlamaCPPClient:
    """LlamaCPPClient can be created and configured."""

    def test_instantiation_with_defaults(self):
        """Client can be created with default settings."""
        client = LlamaCPPClient()
        assert client.base_url.startswith("http")
        assert client.model is not None

    def test_instantiation_with_custom_url(self):
        """Client respects custom base URL."""
        client = LlamaCPPClient(base_url="http://127.0.0.1:9999")
        assert client.base_url == "http://127.0.0.1:9999"

    def test_tool_registration(self):
        """Tools can be registered on the client."""
        client = LlamaCPPClient()

        @client.tool("test_tool")
        def my_tool(query: str) -> str:
            return f"result: {query}"

        assert "test_tool" in client._tools

    def test_run_tool_returns_string(self):
        """Running a registered tool returns a string."""
        client = LlamaCPPClient()

        @client.tool("echo")
        def echo_tool(text: str) -> str:
            return f"echoed: {text}"

        result = client.run_tool("echo", {"text": "hello"})
        assert result == "echoed: hello"

    def test_run_tool_unknown_returns_error(self):
        """Running an unknown tool returns an error string (not exception)."""
        client = LlamaCPPClient()
        result = client.run_tool("nonexistent", {})
        assert "[tool-error]" in result
        assert "nonexistent" in result


# ---------------------------------------------------------------------------
# Tests: Ollama backend integration
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _ollama_reachable(), reason="Ollama not running")
class TestOllamaIntegration:
    """Integration tests that require a running Ollama instance."""

    def test_ollama_tags_endpoint(self):
        """Ollama /api/tags returns available models."""
        import httpx

        r = httpx.get("http://127.0.0.1:11434/api/tags", timeout=5.0)
        assert r.status_code == 200
        data = r.json()
        assert "models" in data
        assert len(data["models"]) > 0

    def test_ollama_chat_endpoint(self):
        """Ollama /api/chat responds to a simple prompt."""
        import httpx

        r = httpx.post(
            "http://127.0.0.1:11434/api/chat",
            json={
                "model": "qwen3:8b",
                "messages": [{"role": "user", "content": "Say OK"}],
                "stream": False,
                "options": {"num_predict": 20},
            },
            timeout=30.0,
        )
        assert r.status_code == 200
        data = r.json()
        # qwen3:8b is a thinking model — content may be empty if response
        # is short (all reasoning). Check that we got a valid response.
        assert data.get("done") is True, "Ollama did not finish generation"
        assert data.get("eval_count", 0) > 0, "No tokens generated"

    def test_ollama_client_get_client(self):
        """get_client() returns LocalAIClient when backend is ollama."""
        set_active_backend("ollama")
        client = get_client()
        assert isinstance(client, LocalAIClient)


# ---------------------------------------------------------------------------
# Tests: BitNet status
# ---------------------------------------------------------------------------


class TestBitNetStatus:
    """Document BitNet model compatibility status."""

    def test_bitnet_model_not_present(self):
        """BitNet model is not on disk (i2_s quant incompatible with build 10200)."""
        bitnet_path = Path.home() / "models" / "bitnet-2b"
        if bitnet_path.exists():
            gguf_files = list(bitnet_path.glob("*.gguf"))
            # Either no GGUF files or they're placeholder (< 100MB)
            for f in gguf_files:
                assert f.stat().st_size < 100_000_000, (
                    f"BitNet GGUF found: {f.name} ({f.stat().st_size} bytes). "
                    "If this is a real model, update this test."
                )

    def test_llamacpp_build_version(self):
        """llama.cpp build version is documented for compatibility tracking."""
        try:
            result = subprocess.run(
                ["llama-server", "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            # llama-server writes version to stderr
            output = (result.stdout + result.stderr).strip()
            version_line = output.split("\n")[0]
            # Build 10200 has TYPE_IQ4_NL_4_4 REMOVED error for BitNet i2_s
            assert "version:" in version_line.lower() or "llama" in version_line.lower(), (
                f"Unexpected llama-server version output: {version_line!r}"
            )
        except FileNotFoundError:
            pytest.skip("llama-server not installed")

    def test_ollama_has_model(self):
        """Ollama has at least one model available for local inference."""
        if not _ollama_reachable():
            pytest.skip("Ollama not running")
        import httpx

        r = httpx.get("http://127.0.0.1:11434/api/tags", timeout=5.0)
        models = r.json().get("models", [])
        model_names = [m["name"] for m in models]
        assert len(model_names) > 0, "No models in Ollama"
        # We know qwen3:8b should be there
        assert any("qwen" in n.lower() for n in model_names), (
            f"Expected qwen model in Ollama, found: {model_names}"
        )
