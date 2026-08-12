"""Cockpit tests — page, aggregated API, and the find-box."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from msb_v3.api.app import create_app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


def test_cockpit_html_serves_page(client: TestClient) -> None:
    r = client.get("/cockpit")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "MSB COCKPIT" in r.text
    assert "/cockpit/api" in r.text  # the page fetches the aggregated data


def test_cockpit_api_shape_with_error_containment(client: TestClient) -> None:
    """Every panel is a dict; failures become {error} panels — the endpoint
    must not 500 even if a probe target is down (asserted structurally so the
    test does not depend on a live server)."""
    r = client.get("/cockpit/api")
    assert r.status_code == 200
    body = r.json()
    for key in ("services", "mission", "governance", "hygiene", "audit", "vault", "research", "memory", "errors"):
        assert key in body, f"missing panel {key}"
        assert isinstance(body[key], dict), f"panel {key} is not a dict"
    assert "ts" in body


def test_cockpit_find_grouped_results(client: TestClient) -> None:
    r = client.get("/cockpit/find", params={"q": "sovereign"})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"query", "vault", "audit", "research"}
    assert isinstance(body["vault"], list)
    assert isinstance(body["audit"], list)
    assert isinstance(body["research"], list)


def test_cockpit_find_empty_query_is_clean(client: TestClient) -> None:
    r = client.get("/cockpit/find")  # no q
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == ""
    assert body["vault"] == [] and body["audit"] == [] and body["research"] == []


def test_cockpit_page_not_registered_at_root(client: TestClient) -> None:
    """The cockpit owns /cockpit only — the existing root dashboard stays."""
    r = client.get("/")
    assert r.status_code == 200
    assert "MSB COCKPIT" not in r.text
