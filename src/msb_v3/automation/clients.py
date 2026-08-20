"""Provider clients for the automation brain.

Every client is fail-closed: ``available()`` is False until its key/URL is
configured, and ``unavailable_reason()`` says exactly what to set. A
creation against an unavailable provider is a ``blocked`` manifest entry,
never a silent no-op or a crash.

- n8n: first-class target — self-hosted, free to run, real REST API
  (``/api/v1/workflows`` with ``X-N8N-API-KEY``). Creates and activates a
  real webhook workflow.
- Make: the practical surface is a webhook trigger (scenario creation needs
  account-level API access that Make does not expose generally).
- Zapier: the REST API cannot create zaps; the configured hook can be
  triggered. Honest scaffold, not a fake.
- GoHighLevel: REST API (``/v1/workflows``, bearer key + Version header).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

import httpx

from msb_v3.core.config import settings

logger = logging.getLogger(__name__)

PROVIDERS = ("n8n", "make", "zapier", "ghl")


def _safe_name(name: str, fallback: str = "msb-automation") -> str:
    return "".join(c if c.isalnum() or c in "-_ " else " " for c in (name or "").strip())[:80] or fallback


def build_n8n_webhook_workflow(name: str, description: str, path: Optional[str] = None) -> Dict[str, Any]:
    """A real, activatable n8n workflow: Webhook (POST) → Respond to Webhook
    echoing the payload. The webhook URL is ``<n8n>/webhook/<path>``."""
    path = (path or f"msb-{uuid.uuid4().hex[:8]}").strip("/")
    return {
        "name": _safe_name(name),
        "nodes": [
            {
                "parameters": {
                    "httpMethod": "POST",
                    "path": path,
                    "responseMode": "onReceived",
                    "options": {},
                },
                "id": uuid.uuid4().hex[:16],
                "name": "Webhook",
                "type": "n8n-nodes-base.webhook",
                "typeVersion": 1.1,
                "position": [0, 0],
            },
            {
                "parameters": {
                    "respondWith": "json",
                    "responseBody": "={{ $json }}",
                    "options": {"responseHeaders": {}},
                },
                "id": uuid.uuid4().hex[:16],
                "name": "Respond",
                "type": "n8n-nodes-base.respondToWebhook",
                "typeVersion": 1,
                "position": [220, 0],
            },
        ],
        "connections": {"Webhook": {"main": [[{"node": "Respond", "type": "main", "index": 0}]]}},
        "settings": {"executionOrder": "v1"},
        "tags": [{"name": "msb-v3"}],
        "meta": {"description": (description or "")[:300]},
    }


class N8nClient:
    """n8n public REST API client (self-hosted, free)."""

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout_s: float = 20.0,
        transport: Any = None,
    ) -> None:
        self.base_url = (base_url or settings.n8n_base_url).rstrip("/")
        self.api_key = api_key if api_key is not None else settings.n8n_api_key
        self.timeout_s = timeout_s
        self._transport = transport

    def available(self) -> bool:
        return bool(self.api_key)

    def unavailable_reason(self) -> str:
        return "N8N_API_KEY not set (create one in n8n: Settings → API)"

    def _request(self, method: str, path: str, **kw: Any) -> Any:
        headers = {"X-N8N-API-KEY": self.api_key, "Content-Type": "application/json"}
        with httpx.Client(timeout=self.timeout_s, transport=self._transport) as client:
            resp = client.request(method, f"{self.base_url}{path}", headers=headers, **kw)
            resp.raise_for_status()
            return resp.json()

    def list_workflows(self) -> List[Dict[str, Any]]:
        return self._request("GET", "/api/v1/workflows")

    def create_workflow(self, workflow: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("POST", "/api/v1/workflows", json=workflow)

    def set_active(self, workflow_id: str, active: bool = True) -> Dict[str, Any]:
        return self._request("PATCH", f"/api/v1/workflows/{workflow_id}", json={"active": active})

    def webhook_url(self, path: str) -> str:
        return f"{self.base_url}/webhook/{path.strip('/')}"


class MakeClient:
    """Make (Integromat) — webhook trigger surface (scenario creation is not
    exposed by Make's API; the configured webhook can be fired)."""

    def __init__(self, *, webhook_url: Optional[str] = None, timeout_s: float = 20.0, transport: Any = None) -> None:
        self.webhook_url = (webhook_url or settings.make_webhook_url or "").strip()
        self.timeout_s = timeout_s
        self._transport = transport

    def available(self) -> bool:
        return bool(self.webhook_url)

    def unavailable_reason(self) -> str:
        return "MSB_MAKE_WEBHOOK_URL not set"

    def create_workflow(self, name: str, description: str) -> Dict[str, Any]:
        """Scaffold: Make scenario creation requires account-level API
        access; the honest deliverable is the webhook trigger URL."""
        return {
            "ok": True,
            "scaffold": True,
            "name": _safe_name(name),
            "description": description[:300],
            "webhook_url": self.webhook_url,
            "note": "Make scenario creation is not exposed by Make's API; trigger the configured webhook to run the scenario.",
        }

    def trigger(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        with httpx.Client(timeout=self.timeout_s, transport=self._transport) as client:
            resp = client.post(self.webhook_url, json=payload)
            resp.raise_for_status()
            return {"ok": resp.status_code < 400, "status_code": resp.status_code}


class ZapierClient:
    """Zapier — webhook trigger surface (Zapier's REST API cannot create
    zaps; the configured hook can be fired)."""

    def __init__(self, *, api_key: Optional[str] = None, timeout_s: float = 20.0, transport: Any = None) -> None:
        self.api_key = api_key if api_key is not None else settings.zapier_api_key
        self.timeout_s = timeout_s
        self._transport = transport

    def available(self) -> bool:
        return bool(self.api_key)

    def unavailable_reason(self) -> str:
        return "MSB_ZAPIER_API_KEY not set"

    def create_workflow(self, name: str, description: str) -> Dict[str, Any]:
        return {
            "ok": True,
            "scaffold": True,
            "name": _safe_name(name),
            "description": description[:300],
            "note": "Zapier's REST API cannot create zaps; trigger a configured Zapier webhook to run an existing zap.",
        }


class GhlClient:
    """GoHighLevel REST API (workflows)."""

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        location_id: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout_s: float = 20.0,
        transport: Any = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.ghl_api_key
        self.location_id = location_id if location_id is not None else settings.ghl_location_id
        self.base_url = (base_url or settings.ghl_base_url).rstrip("/")
        self.timeout_s = timeout_s
        self._transport = transport

    def available(self) -> bool:
        return bool(self.api_key)

    def unavailable_reason(self) -> str:
        return "MSB_GHL_API_KEY not set"

    def create_workflow(self, name: str, description: str) -> Dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Version": "2021-07-28",
        }
        body: Dict[str, Any] = {"name": _safe_name(name), "description": (description or "")[:300], "type": 0}
        if self.location_id:
            body["locationId"] = self.location_id
        with httpx.Client(timeout=self.timeout_s, transport=self._transport) as client:
            resp = client.post(f"{self.base_url}/v1/workflows", json=body, headers=headers)
            resp.raise_for_status()
            return resp.json()


def get_client(provider: str) -> Any:
    """Deterministic client factory; unknown providers fail closed."""
    provider = (provider or "").strip().lower()
    if provider == "n8n":
        return N8nClient()
    if provider == "make":
        return MakeClient()
    if provider == "zapier":
        return ZapierClient()
    if provider == "ghl":
        return GhlClient()
    raise ValueError(f"unknown automation provider: {provider!r} (known: {PROVIDERS})")


def provider_status() -> Dict[str, Any]:
    """Configured/available status of every provider (for /automation/status)."""
    out: Dict[str, Any] = {}
    for provider in PROVIDERS:
        client = get_client(provider)
        out[provider] = {
            "configured": client.available(),
            "reason": "" if client.available() else client.unavailable_reason(),
        }
    return out
