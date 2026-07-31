"""Shared client factory for local AI backends."""

from __future__ import annotations

from msb_v3.local_ai.llama_client import LlamaCPPClient
from msb_v3.local_ai.ollama import LocalAIClient

_active_backend: str = "ollama"


def get_client() -> LocalAIClient | LlamaCPPClient:
    if _active_backend == "llamacpp":
        return LlamaCPPClient()
    return LocalAIClient()


def set_active_backend(backend: str) -> None:
    global _active_backend
    if backend in {"ollama", "llamacpp"}:
        _active_backend = backend


def active_backend() -> str:
    return _active_backend
