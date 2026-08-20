"""Tests for the automation provider clients (automation/clients.py).

n8n / GHL clients are exercised against httpx MockTransport stubs so the
exact API contract (paths, headers, body) is pinned. Availability is
fail-closed: no key/URL = unavailable with a reason, never a crash.
"""

from __future__ import annotations

import json

import httpx
import pytest

from msb_v3.automation.clients import (
    GhlClient,
    MakeClient,
    N8nClient,
    ZapierClient,
    build_n8n_forwarder_workflow,
    build_n8n_webhook_workflow,
    get_client,
    provider_status,
)


def test_n8n_unavailable_without_key(monkeypatch) -> None:
    monkeypatch.setattr("msb_v3.core.config.settings.n8n_api_key", "")
    client = N8nClient()
    assert client.available() is False
    assert "N8N_API_KEY" in client.unavailable_reason()


def test_n8n_create_workflow_contract() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["key"] = request.headers.get("X-N8N-API-KEY")
        body = json.loads(request.content)
        captured["body"] = body
        return httpx.Response(200, json={"id": "wf-42", "name": body["name"], "active": False})

    transport = httpx.MockTransport(handler)
    client = N8nClient(base_url="http://127.0.0.1:5678", api_key="n8n-test-key", transport=transport)

    workflow = build_n8n_webhook_workflow("echo bot", "echo payloads", path="echo-bot")
    created = client.create_workflow(workflow)
    assert created["id"] == "wf-42"
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/v1/workflows"
    assert captured["key"] == "n8n-test-key"
    assert captured["body"]["name"] == "echo bot"
    assert captured["body"]["connections"]["Webhook"]["main"][0][0]["node"] == "Respond"


def test_n8n_set_active_contract() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "wf-42", "active": True})

    transport = httpx.MockTransport(handler)
    client = N8nClient(base_url="http://127.0.0.1:5678", api_key="k", transport=transport)
    client.set_active("wf-42", active=True)
    assert captured["method"] == "PATCH"
    assert captured["path"] == "/api/v1/workflows/wf-42"
    assert captured["body"] == {"active": True}


def test_n8n_forwarder_workflow_points_at_hook() -> None:
    """The platform-as-pointer primitive: Webhook trigger → HTTP Request
    forwarding the payload to msb-v3's /hook."""
    wf = build_n8n_forwarder_workflow("http://127.0.0.1:8766/hook/auto-abc", path="msb-fwd")
    nodes = {n["name"]: n for n in wf["nodes"]}
    webhook = nodes["Webhook"]
    forward = nodes["Forward to msb-v3"]
    assert webhook["parameters"]["path"] == "msb-fwd"
    assert forward["parameters"]["method"] == "POST"
    assert forward["parameters"]["url"] == "http://127.0.0.1:8766/hook/auto-abc"
    assert forward["parameters"]["jsonBody"] == "={{ JSON.stringify($json) }}"
    assert wf["connections"]["Webhook"]["main"][0][0]["node"] == "Forward to msb-v3"


def test_n8n_webhook_url() -> None:
    client = N8nClient(base_url="http://127.0.0.1:5678", api_key="k")
    assert client.webhook_url("echo-bot") == "http://127.0.0.1:5678/webhook/echo-bot"


def test_ghl_create_webhook_contract() -> None:
    """The perceiver's GHL reach primitive: register an outbound webhook at
    the location so GHL fires events at msb-v3's /hook."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["auth"] = request.headers.get("Authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"webhookId": "wh-1"})

    transport = httpx.MockTransport(handler)
    client = GhlClient(
        api_key="ghl-key",
        location_id="loc-1",
        base_url="https://services.leadconnectorhq.com",
        transport=transport,
    )
    result = client.create_webhook("https://public.example.com/hook/ghl-abc", ["ContactCreate", "FormSubmit"])
    assert result["webhookId"] == "wh-1"
    assert captured["method"] == "POST"
    assert captured["path"] == "/v1/webhooks"
    assert captured["auth"] == "Bearer ghl-key"
    assert captured["body"]["url"] == "https://public.example.com/hook/ghl-abc"
    assert captured["body"]["events"] == ["ContactCreate", "FormSubmit"]
    assert captured["body"]["locationId"] == "loc-1"


def test_ghl_create_webhook_requires_url_and_events() -> None:
    client = GhlClient(api_key="k", base_url="https://services.leadconnectorhq.com")
    with pytest.raises(ValueError):
        client.create_webhook("", ["ContactCreate"])
    with pytest.raises(ValueError):
        client.create_webhook("https://x.example.com/hook", [])


def test_ghl_create_workflow_contract() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["auth"] = request.headers.get("Authorization")
        captured["version"] = request.headers.get("Version")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"workflow": {"id": "wf-1"}})

    transport = httpx.MockTransport(handler)
    client = GhlClient(api_key="ghl-key", base_url="https://services.leadconnectorhq.com", transport=transport)
    client.create_workflow("lead followup", "nudge leads")
    assert captured["method"] == "POST"
    assert captured["path"] == "/v1/workflows"
    assert captured["auth"] == "Bearer ghl-key"
    assert captured["version"] == "2021-07-28"
    assert captured["body"]["name"] == "lead followup"


def test_make_and_zapier_availability(monkeypatch) -> None:
    monkeypatch.setattr("msb_v3.core.config.settings.make_webhook_url", "")
    monkeypatch.setattr("msb_v3.core.config.settings.zapier_api_key", "")
    make = MakeClient()
    zapier = ZapierClient()
    assert make.available() is False
    assert zapier.available() is False
    assert "MSB_MAKE_WEBHOOK_URL" in make.unavailable_reason()
    assert "MSB_ZAPIER_API_KEY" in zapier.unavailable_reason()

    # Scaffolds are honest about what the platform API can't do.
    make_on = MakeClient(webhook_url="https://hook.make.com/abc")
    note = make_on.create_workflow("x", "y")
    assert note["scaffold"] is True
    assert "webhook" in note["note"].lower()


def test_get_client_unknown_provider() -> None:
    with pytest.raises(ValueError):
        get_client("salesforce")


def test_provider_status(monkeypatch) -> None:
    monkeypatch.setattr("msb_v3.core.config.settings.n8n_api_key", "")
    monkeypatch.setattr("msb_v3.core.config.settings.make_webhook_url", "")
    monkeypatch.setattr("msb_v3.core.config.settings.zapier_api_key", "")
    monkeypatch.setattr("msb_v3.core.config.settings.ghl_api_key", "")
    status = provider_status()
    assert set(status) == {"n8n", "make", "zapier", "ghl"}
    assert all(s["configured"] is False for s in status.values())
    assert all(s["reason"] for s in status.values())
