"""Tests for Triumvirate Prometheus metrics emission."""
from __future__ import annotations

import os

import httpx
import pytest

BASE = os.environ.get("MSB_BASE_URL", "http://127.0.0.1:8766")

# Server-integration: asserts against a running msb-v3 on MSB_BASE_URL / :8766.
# Tier: integration (PRODUCTION-CLOSURE-001 P1) — not part of the hermetic core.
pytestmark = pytest.mark.integration
_METRIC_NAMES = [
    "msb_v3_triumvirate_plan_total",
    "msb_v3_triumvirate_lock_total",
    "msb_v3_triumvirate_audit_total",
    "msb_v3_triumvirate_scan_total",
    "msb_v3_triumvirate_peer_ops_total",
    "msb_v3_triumvirate_hippocampus_total",
    "msb_v3_triumvirate_multimodal_total",
]


def _post(path, body=None, accept=(200,)):
    with httpx.Client(timeout=10.0) as client:
        r = client.post(f"{BASE}{path}", json=body or {}, headers={"content-type": "application/json"})
    assert r.status_code in accept, f"POST {path} -> {r.status_code}"


def test_triumvirate_metrics_emitted():
    _post("/triumvirate/plan", {"goal": "metrics check"})
    _post("/triumvirate/status/lock", {"goal": "metrics lock"})
    _post("/triumvirate/argus/audit", {})
    _post("/triumvirate/guardian/scan", {"script": "print('hi')"})
    _post("/triumvirate/cluster/peers", {"node_id": "n-metrics", "host": "localhost", "port": 8766})
    _post("/triumvirate/hippocampus/upsert", {"doc_id": "d1", "chunk_id": "c1", "text": "t", "embedding": [1.0]})
    # /multimodal/* is fail-closed by default: the live server returns 503
    # unless the operator explicitly set MSB_MULTIMODAL_ENABLED=1 when it
    # started, which this client-side test cannot change. The metric family
    # is always registered, so the assertion below still guards emission.
    _post("/triumvirate/multimodal/vision/capture", {}, accept=(200, 503))
    _post("/triumvirate/multimodal/haptic/heartbeat", {}, accept=(200, 503))
    _post("/triumvirate/multimodal/speech/command", {"transcript": "hello"}, accept=(200, 503))

    with httpx.Client(timeout=10.0) as client:
        r = client.get(f"{BASE}/metrics/prometheus", headers={"accept": "text/plain"})
    assert r.status_code == 200, f"metrics -> {r.status_code}"
    body = r.text
    for name in _METRIC_NAMES:
        assert name in body, f"missing metric {name}"
