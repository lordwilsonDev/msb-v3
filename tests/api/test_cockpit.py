"""Cockpit tests — page, aggregated API, and the find-box."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import msb_v3.api.cockpit as cockpit_api
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
    # the GUARDS card replaced the GOVERNANCE BRAKES card
    assert 'data-panel="guards"' in r.text
    assert "GOVERNANCE BRAKES" not in r.text


def test_cockpit_api_shape_with_error_containment(client: TestClient) -> None:
    """Every panel is a dict; failures become {error} panels — the endpoint
    must not 500 even if a probe target is down (asserted structurally so the
    test does not depend on a live server)."""
    r = client.get("/cockpit/api")
    assert r.status_code == 200
    body = r.json()
    for key in ("services", "mission", "flywheel", "guards", "limits", "hygiene", "audit", "vault", "research", "memory", "errors"):
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


def test_cockpit_api_containment_with_all_probes_down(client: TestClient, monkeypatch) -> None:
    """Exercises the containment path for real: point the self-probe base at a
    closed port so every HTTP probe fails — the endpoint must still return 200
    with error panels, never a 500 (one dead service costs a panel, not the
    page). In-process panels (governance/hygiene/audit/vault/mission/errors)
    must still succeed."""
    monkeypatch.setattr(cockpit_api, "_MSB_BASE", "http://127.0.0.1:9")  # closed port
    r = client.get("/cockpit/api")
    assert r.status_code == 200
    body = r.json()
    # Probe-level containment: each HTTP probe reports its own error, the
    # panel still renders.
    assert "error" in body["services"]["status"]
    assert "error" in body["services"]["models"]
    for key in ("guards", "limits", "hygiene", "audit", "vault", "mission", "flywheel", "errors"):
        assert "error" not in body[key], f"in-process panel {key} must not fail"


def test_cockpit_find_contained_when_research_dir_unreadable(client: TestClient, monkeypatch, tmp_path) -> None:
    """The find-box must not 500 when the research dir cannot be listed."""
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    monkeypatch.setattr(cockpit_api, "_RESEARCH_DIR", blocker)
    monkeypatch.setattr(cockpit_api, "_MSB_BASE", "http://127.0.0.1:9")
    r = client.get("/cockpit/find", params={"q": "sovereign"})
    assert r.status_code == 200
    body = r.json()
    assert body["research"] == []
    assert body["vault"] == []  # probe down -> contained empty


def test_cockpit_api_rate_limit_panel_reflects_rejections(client: TestClient) -> None:
    """The RATE LIMIT REJECTIONS panel reads the live shared counter — an
    increment from the /v1 guard is visible on the cockpit, then restored
    so the global counter is not left polluted."""
    from msb_v3.observability.metrics import RATE_LIMIT_REJECTIONS

    label = RATE_LIMIT_REJECTIONS.labels(limiter="chat", reason="rate")
    before = label._value.get()
    try:
        label.inc()
        r = client.get("/cockpit/api")
        assert r.status_code == 200
        limits = r.json()["limits"]
        assert "error" not in limits
        assert limits["total"] >= before + 1
        entry = next(
            c for c in limits["counters"] if c["limiter"] == "chat" and c["reason"] == "rate"
        )
        assert entry["count"] == before + 1
        # one entry per (limiter, reason) combo and counts only — the
        # prometheus _created timestamp samples must never leak in
        combos = [(c["limiter"], c["reason"]) for c in limits["counters"]]
        assert len(combos) == len(set(combos))
        assert all(isinstance(c["count"], int) and c["count"] < 1_000_000 for c in limits["counters"])
    finally:
        label._value.set(before)  # restore — the counter is global state


def test_cockpit_guards_panel_merges_caps_brakes_policy(client: TestClient, monkeypatch) -> None:
    """The GUARDS panel is the one overview: live brake state (kill switch,
    budgets, approvals pending, governor signals) merged with the configured
    caps + approval policy from the shared guard_config() builder — the
    same source /system/config and both CLIs serve, so it cannot drift.
    Live-read: a settings change is visible on the next call, no restart."""
    from msb_v3.core.config import settings
    from msb_v3.core.guard_config import guard_config

    r = client.get("/cockpit/api")
    assert r.status_code == 200
    g = r.json()["guards"]
    assert "error" not in g
    # live brake state rides along
    assert "killswitch" in g and "budgets" in g
    assert "approvals_pending" in g and "governor_history" in g
    # the three config areas come from the one builder — exact equality
    assert g["caps"] == guard_config()["rate_limits"]
    assert g["brakes"] == guard_config()["governance"]
    assert g["approval_policy"] == guard_config()["approvals"]

    monkeypatch.setattr(settings, "openai_chat_rate_max", 9)
    assert client.get("/cockpit/api").json()["guards"]["caps"]["OPENAI_CHAT_RATE_MAX"] == 9
