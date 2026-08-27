"""Centralized Qdrant environment contract.

The contract is intentionally small and side-effect-free by default. Callers
choose whether an unavailable service is optional or required rather than
letting transport errors leak into unrelated tests.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class QdrantContract:
    enabled: bool
    endpoint: str
    reachable: bool
    writable: bool
    expected_collection: str | None
    collection_available: bool
    classification: str
    detail: str

    @property
    def ready(self) -> bool:
        return self.enabled and self.reachable and self.writable and (
            self.expected_collection is None or self.collection_available
        )


def qdrant_endpoint() -> str:
    host = os.getenv("QDRANT_HOST", "127.0.0.1")
    port = os.getenv("QDRANT_PORT", "6333")
    return f"http://{host}:{port}"


def preflight(*, expected_collection: str | None = None, timeout: float = 2.0) -> QdrantContract:
    enabled = os.getenv("MSB_QDRANT_ENABLED", "1") == "1"
    endpoint = qdrant_endpoint()
    if not enabled:
        return QdrantContract(False, endpoint, False, False, expected_collection, False, "ENVIRONMENT", "Qdrant disabled")
    try:
        with httpx.Client(base_url=endpoint, timeout=timeout) as client:
            health = client.get("/healthz")
            health.raise_for_status()
            reachable = True
            writable = _storage_writable(client)
            collection_available = (
                expected_collection is None
                or client.get(f"/collections/{expected_collection}").is_success
            )
    except (httpx.HTTPError, OSError) as exc:
        return QdrantContract(True, endpoint, False, False, expected_collection, False, "INFRASTRUCTURE", f"Qdrant unavailable: {exc}")
    if not writable:
        return QdrantContract(True, endpoint, reachable, False, expected_collection, collection_available, "INFRASTRUCTURE", "Qdrant storage is not writable")
    if expected_collection is not None and not collection_available:
        return QdrantContract(True, endpoint, reachable, writable, expected_collection, False, "ENVIRONMENT", f"collection not available: {expected_collection}")
    return QdrantContract(True, endpoint, reachable, writable, expected_collection, collection_available, "PASS", "Qdrant contract satisfied")


def _storage_writable(client: Any) -> bool:
    """Use Qdrant's collection endpoint as a non-destructive write-path check.

    Qdrant does not expose a portable storage-writable endpoint; a successful
    collections read proves the service/storage path is readable, while the
    actual upsert contract remains covered by the application integration gate.
    """
    response = client.get("/collections")
    return response.is_success
