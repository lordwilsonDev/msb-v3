"""Phase 1.4 — the ApplicationContainer composition root and DI accessors."""

from __future__ import annotations

from msb_v3.core.container import (
    ApplicationContainer,
    build_container,
    get_container,
    get_container_dep,
    reset_container,
    set_container,
)
from msb_v3.retrieval.vector_store import SQLiteVectorStore


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
