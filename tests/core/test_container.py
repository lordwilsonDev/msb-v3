"""Phase 1.4 — the ApplicationContainer composition root and DI accessors."""

from __future__ import annotations

import pytest

from msb_v3.core.config import settings
from msb_v3.core.container import (
    ApplicationContainer,
    build_container,
    get_container,
    get_container_dep,
    reset_container,
    set_container,
)
from msb_v3.retrieval.vector_store import QdrantVectorStore, SQLiteVectorStore


def test_build_container_populates_all_services() -> None:
    container = build_container()
    for name in (
        "planner",
        "anchor",
        "guardian",
        "sbom",
        "poison_pill",
        "argus",
        "cluster_discovery",
        "hippocampus",
        "event_bus",
        "identity",
        "memory_store",
        "conversation_stub",
    ):
        assert getattr(container, name) is not None, f"{name} not built"


def test_build_container_overrides_one_service(tmp_path) -> None:
    store = SQLiteVectorStore(db_path=tmp_path / "v.db", tenant_id="t")
    container = build_container(hippocampus=store)
    assert container.hippocampus is store
    assert container.planner is not None  # other services stay real


def test_set_and_reset_container(tmp_path) -> None:
    reset_container()
    container = build_container(hippocampus=SQLiteVectorStore(db_path=tmp_path / "v.db", tenant_id="t"))
    set_container(container)
    assert get_container() is container
    reset_container()
    assert get_container() is not None  # lazily rebuilt


class _FakeRequest:
    """Duck-typed like a FastAPI Request for get_container_dep's needs."""

    def __init__(self, container: ApplicationContainer | None) -> None:
        self.app = type("_App", (), {"state": type("_State", (), {"container": container})()})()


def test_get_container_dep_prefers_app_state() -> None:
    mine = build_container()
    assert get_container_dep(_FakeRequest(mine)) is mine


def test_get_container_dep_falls_back_to_default() -> None:
    reset_container()
    got = get_container_dep(_FakeRequest(None))
    assert isinstance(got, ApplicationContainer)


# --- hippocampus backend selection (the VectorStore seam) -------------------
# The backend is selected from configuration, not hardcoded: unset keeps the
# always-available SQLite store, and MSB_VECTOR_BACKEND swaps it with no change
# to the consumer (api/triumvirate.py only ever touches container.hippocampus).


def test_hippocampus_defaults_to_sqlite_when_unconfigured(monkeypatch) -> None:
    """Unconfigured deployments keep the always-available local store, so
    hippocampus never blocks on a remote Qdrant."""
    monkeypatch.setattr(settings, "vector_backend", "")
    assert isinstance(build_container().hippocampus, SQLiteVectorStore)


def test_hippocampus_follows_the_configured_backend(monkeypatch) -> None:
    """The swap: MSB_VECTOR_BACKEND selects the backend and the container
    follows it, with no consumer change."""
    monkeypatch.setattr(settings, "vector_backend", "qdrant")
    assert isinstance(build_container().hippocampus, QdrantVectorStore)


def test_unknown_vector_backend_fails_closed(monkeypatch) -> None:
    """A typo in config must raise at composition time, not silently fall back
    to a different store than the operator asked for."""
    monkeypatch.setattr(settings, "vector_backend", "milvus")
    with pytest.raises(ValueError, match="unknown vector backend"):
        build_container()


def test_vector_backend_setting_reads_the_env(monkeypatch) -> None:
    """MSB_VECTOR_BACKEND reaches the container through the settings field
    (settings is import-time, so the field's own factory is what reads it)."""
    factory = type(settings).__dataclass_fields__["vector_backend"].default_factory

    monkeypatch.setenv("MSB_VECTOR_BACKEND", "qdrant")
    assert factory() == "qdrant"

    monkeypatch.setenv("MSB_VECTOR_BACKEND", "  ")
    assert factory() == "", "whitespace-only config must not select a backend"

    monkeypatch.delenv("MSB_VECTOR_BACKEND")
    assert factory() == ""
