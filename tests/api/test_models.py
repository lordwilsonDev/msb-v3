"""Tests for /models router."""

from __future__ import annotations

import pytest

from msb_v3.core.config import settings
from msb_v3.api.models import _list_models, _switch_backend, get_client, _active_backend


def test_list_models():
    models = _list_models()
    assert len(models) == 2
    backends = {m["backend"] for m in models}
    assert backends == {"ollama", "llamacpp"}


def test_switch_backend_ollama(monkeypatch):
    monkeypatch.setattr("msb_v3.api.models._active_backend", "llamacpp", raising=False)
    result = _switch_backend({"backend": "ollama"})
    assert result["status"] == "ok"
    assert result["backend"] == "ollama"


def test_switch_backend_llamacpp(monkeypatch):
    monkeypatch.setattr("msb_v3.api.models._active_backend", "ollama", raising=False)
    result = _switch_backend({"backend": "llamacpp"})
    assert result["status"] == "ok"
    assert result["backend"] == "llamacpp"


def test_switch_backend_invalid():
    result = _switch_backend({"backend": "unknown"})
    assert result["status"] == "error"


def test_get_client_ollama(monkeypatch):
    monkeypatch.setattr("msb_v3.api.models._active_backend", "ollama", raising=False)
    client = get_client()
    assert client.__class__.__name__ == "LocalAIClient"


def test_get_client_llamacpp(monkeypatch):
    monkeypatch.setattr("msb_v3.api.models._active_backend", "llamacpp", raising=False)
    client = get_client()
    assert client.__class__.__name__ == "LlamaCPPClient"
