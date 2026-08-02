"""Shared client factory for local AI backends."""

from __future__ import annotations

from msb_v3.core.config import settings
from msb_v3.local_ai.llama_client import LlamaCPPClient
from msb_v3.local_ai.ollama import LocalAIClient


def get_client() -> LocalAIClient | LlamaCPPClient:
    if settings._active_backend == "llamacpp":
        return LlamaCPPClient()
    return LocalAIClient()


def set_active_backend(backend: str) -> None:
    if backend in {"ollama", "llamacpp"}:
        settings._active_backend = backend


def active_backend() -> str:
    return settings._active_backend
