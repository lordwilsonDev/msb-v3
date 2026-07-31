"""Tests for Triumvirate Phase 3 — Guardian Protocol."""
from __future__ import annotations

import os
import tempfile

import pytest

from msb_v3.triumvirate.guardian_scanner import GuardianScanner, PoisonPill, SBOMRegistry


def test_scanner_blocks_dangerous_patterns():
    scanner = GuardianScanner()
    bad = "import os\nos.system('rm -rf /')\n"
    report = scanner.scan_script(bad)
    assert report.blocked is True
    assert report.risk == "HIGH"


def test_scanner_allows_clean_script():
    scanner = GuardianScanner()
    clean = "print('hello')\n"
    report = scanner.scan_script(clean)
    assert report.blocked is False
    assert report.risk == "LOW"


def test_sbom_registry_round_trip():
    with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as tf:
        tf.write(b"print(1)\n")
        path = tf.name
    try:
        registry = SBOMRegistry()
        registry.register("server-a", path, {"version": "1.0.0"})
        assert registry.trusted("server-a") is True
        assert registry.trusted("server-b") is False
        scanner = GuardianScanner()
        assert scanner.verify_sbom("server-a", path) is True
        assert scanner.verify_sbom("server-b") is False
    finally:
        os.unlink(path)


def test_least_privilege_allows_matching_scope():
    scanner = GuardianScanner()
    assert scanner.enforce_least_privilege("sub-agent-token-1", "read") is True


def test_least_privilege_blocks_cross_scope():
    scanner = GuardianScanner()
    assert scanner.enforce_least_privilege("sub-agent-token-1", "admin") is False


def test_poison_pill_arms_and_detonates():
    pill = PoisonPill()
    armed = pill.arm()
    assert armed["armed"] is True
    detonated = pill.detonate()
    assert detonated["armed"] is False
    assert "detonated_at" in detonated
