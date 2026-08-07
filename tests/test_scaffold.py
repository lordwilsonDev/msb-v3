"""Tests — sovereign core scaffold."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))


def test_package_imports():
    spec = importlib.util.find_spec("msb_v3")
    assert spec is not None


def test_core_config_loads():
    from msb_v3.core.config import settings

    assert settings.ollama_model in {"deepseek-r1:1.5b", "qwen3:latest", "qwen3:8b"}
    assert settings.port == 8766


def test_local_ai_client_construction():
    from msb_v3.local_ai.ollama import LocalAIClient

    c = LocalAIClient(base_url="http://localhost:11434")
    assert c.model in {"deepseek-r1:1.5b", "qwen3:latest", "qwen3:8b"}


def test_chat_harness_returns_result():
    from msb_v3.harnesses.base import ChatHarness, HarnessResult

    h = ChatHarness()
    result = h.execute("ping")
    assert isinstance(result, HarnessResult)
    assert result.event == "chat:completed"
    assert result.payload["query"] == "ping"
    assert result.ok is True


def test_observability_metrics():
    from msb_v3.observability.metrics import Metrics

    Metrics.set_ready(True)
    Metrics.inc("chat", "chat:completed")
    Metrics.latency("chat", 0.5)


def test_db_connection():
    from msb_v3.db.sqlite import get_connection

    conn = get_connection()
    row = conn.execute("SELECT 1 AS n").fetchone()
    assert row["n"] == 1
    conn.close()
