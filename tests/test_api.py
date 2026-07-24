"""Tests for sovereign core API."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))


def test_app_imports():
    spec = importlib.util.find_spec("msb_v3.api.app")
    assert spec is not None


def test_registry_items_are_unique():
    from msb_v3.api.registry import REGISTRY

    prefixes = [entry["prefix"] for entry in REGISTRY]
    assert len(prefixes) == len(set(prefixes))