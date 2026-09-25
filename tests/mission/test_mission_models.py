"""Mission version hashing (blueprint 2026-09-25 §4)."""

from __future__ import annotations

import pytest

from msb_ledger.audit_v2 import CanonicalizationError
from msb_v3.mission.models import PARENT_KIND, VersionKind, version_sha256


def test_hash_ignores_key_order() -> None:
    a = version_sha256(VersionKind.BLUEPRINT, None, {"goal": "todo", "scope": ["board"]})
    b = version_sha256(VersionKind.BLUEPRINT, None, {"scope": ["board"], "goal": "todo"})
    assert a == b
    assert len(a) == 64


def test_same_body_against_a_different_parent_is_a_different_version() -> None:
    body = {"tasks": ["T-1"]}
    assert version_sha256(VersionKind.PLAN, "a" * 64, body) != version_sha256(
        VersionKind.PLAN, "b" * 64, body
    )


def test_same_body_as_a_different_kind_is_a_different_version() -> None:
    body = {"x": 1}
    assert version_sha256(VersionKind.PLAN, "a" * 64, body) != version_sha256(
        VersionKind.TASK_GRAPH, "a" * 64, body
    )


def test_non_canonical_body_is_refused() -> None:
    with pytest.raises(CanonicalizationError):
        version_sha256(VersionKind.BLUEPRINT, None, {"score": float("nan")})


def test_parent_chain() -> None:
    assert PARENT_KIND[VersionKind.BLUEPRINT] is None
    assert PARENT_KIND[VersionKind.PLAN] is VersionKind.BLUEPRINT
    assert PARENT_KIND[VersionKind.TASK_GRAPH] is VersionKind.PLAN
