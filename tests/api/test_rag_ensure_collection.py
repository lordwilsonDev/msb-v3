from __future__ import annotations

import logging

from msb_v3.api.rag import _ensure_collection


class _Client:
    def __init__(self, exists: bool, create_exc: Exception | None = None) -> None:
        self._exists = exists
        self._create_exc = create_exc
        self.created = False

    def collection_exists(self, name: str) -> bool:
        return self._exists

    def create_collection(self, **kw: object) -> None:
        self.created = True
        if self._create_exc:
            raise self._create_exc


def test_skips_create_when_exists(caplog) -> None:
    c = _Client(exists=True)
    with caplog.at_level(logging.WARNING):
        _ensure_collection(c, "tenant_x")
    assert c.created is False
    assert "failed to create collection" not in caplog.text


def test_creates_when_missing(caplog) -> None:
    c = _Client(exists=False)
    with caplog.at_level(logging.WARNING):
        _ensure_collection(c, "tenant_x")
    assert c.created is True
    assert caplog.text == "" or "failed to create" not in caplog.text


def test_409_on_create_is_swallowed(caplog) -> None:
    c = _Client(exists=False, create_exc=RuntimeError("Unexpected Response: 409 (Conflict) already exists"))
    with caplog.at_level(logging.WARNING):
        _ensure_collection(c, "tenant_x")
    assert "failed to create collection" not in caplog.text


def test_real_failure_is_logged(caplog) -> None:
    c = _Client(exists=False, create_exc=RuntimeError("connection refused"))
    with caplog.at_level(logging.WARNING):
        _ensure_collection(c, "tenant_x")
    assert "failed to create collection tenant_x" in caplog.text
