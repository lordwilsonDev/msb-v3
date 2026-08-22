"""OpenBot adapter — the harness bar, same as DeepSeek.

Pins the contract:

1. A governed ``/openbot/run`` that fails closed (unknown agent → ERROR
   before any model call) still emits EXACTLY ONE evidence receipt with
   ``request_id == run.run_id`` and ``model_calls == 0`` — mirror of
   ``test_deepseek_blocked_run_zero_calls_and_receipt``, through the
   adapter's HTTP surface.
2. Supervisor lifecycle errors map honestly: HTTP error → 502, unreachable
   → 503.
3. The supervisor surface (``/health``, ``/computers/*``) and ``/run`` are
   fail-closed: 503 without a configured operator token, 401 on mismatch.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest
from fastapi.testclient import TestClient

from msb_v3.api.app import create_app
from msb_v3.core.config import settings


def _lines(path: Path) -> list[dict]:
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _open_operator(monkeypatch: pytest.MonkeyPatch, token: str = "tok") -> None:
    monkeypatch.setenv("MSB_OPERATOR_TOKEN", token)
    monkeypatch.setattr(settings, "operator_token", token)


def _client() -> TestClient:
    return TestClient(create_app())


def _stub_supervisor(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
    """Replace urllib's urlopen in the adapter module so supervisor calls
    raise the given exception (no network, no Docker)."""
    import msb_v3.integrations.openbot as openbot_mod

    def raiser(request, timeout=None) -> None:
        raise exc

    monkeypatch.setattr(openbot_mod, "urlopen", raiser)


def _http_error(code: int, body: str) -> HTTPError:
    return HTTPError("http://127.0.0.1:4300/health", code, "err", {}, io.BytesIO(body.encode()))


def _fake_supervisor_response(payload: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """Return a successful supervisor payload from the adapter's urlopen."""
    import msb_v3.integrations.openbot as openbot_mod

    class _FakeResponse:
        def __init__(self, payload: dict) -> None:
            self._payload = payload

        def read(self) -> bytes:
            return json.dumps(self._payload).encode("utf-8")

        def __enter__(self) -> "_FakeResponse":
            return self

        def __exit__(self, *exc) -> None:
            return None

    def responder(request, timeout=None) -> _FakeResponse:
        return _FakeResponse(payload)

    monkeypatch.setattr(openbot_mod, "urlopen", responder)


# --- contract (public, no auth) --------------------------------------------


def test_contract_is_public():
    response = _client().get('/openbot/contract')
    assert response.status_code == 200
    assert response.json()['version'] == '1'
    assert response.json()['fail_closed'] is True


# --- operator gate: fail-closed --------------------------------------------


def test_run_requires_operator(monkeypatch):
    monkeypatch.setattr(settings, 'operator_token', '')
    response = _client().post('/openbot/run', json={'bot_id': 'b1', 'message': 'hello'})
    assert response.status_code == 503


def test_supervisor_surface_requires_operator(monkeypatch):
    """/health and /computers/* touch the supervisor (and Docker behind it) —
    the surface is closed without a configured token and rejects a wrong one
    before any supervisor call."""
    monkeypatch.setattr(settings, "operator_token", "")
    client = _client()
    assert client.get("/openbot/health").status_code == 503
    assert client.get("/openbot/computers").status_code == 503
    assert client.post("/openbot/computers/b1/ensure").status_code == 503
    assert client.post("/openbot/computers/b1/stop").status_code == 503
    assert client.post("/openbot/computers/b1/reset").status_code == 503

    _open_operator(monkeypatch)
    assert client.get("/openbot/health", headers=_auth("wrong")).status_code == 401


# --- supervisor error mapping ----------------------------------------------


def test_health_supervisor_http_error_is_502(monkeypatch):
    _open_operator(monkeypatch)
    _stub_supervisor(monkeypatch, _http_error(500, '{"error": "boom"}'))
    response = _client().get("/openbot/health", headers=_auth("tok"))
    assert response.status_code == 502
    assert "supervisor error 500" in response.json()["detail"]


def test_health_supervisor_unreachable_is_503(monkeypatch):
    _open_operator(monkeypatch)
    _stub_supervisor(monkeypatch, URLError("connection refused"))
    response = _client().get("/openbot/health", headers=_auth("tok"))
    assert response.status_code == 503
    assert "unavailable" in response.json()["detail"]


def test_computers_supervisor_http_error_is_502(monkeypatch):
    _open_operator(monkeypatch)
    _stub_supervisor(monkeypatch, _http_error(404, "not found"))
    response = _client().get("/openbot/computers", headers=_auth("tok"))
    assert response.status_code == 502


def test_health_supervisor_ok(monkeypatch):
    _open_operator(monkeypatch)
    _fake_supervisor_response({"status": "ok", "version": "1"}, monkeypatch)
    response = _client().get("/openbot/health", headers=_auth("tok"))
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["supervisor"]["status"] == "ok"


# --- governed run → exactly one receipt ------------------------------------


def test_run_emits_exactly_one_receipt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Mirror of test_deepseek_blocked_run_zero_calls_and_receipt through the
    adapter: a governed /openbot/run that fails closed (unknown agent → ERROR
    before any model call) emits exactly one evidence receipt with
    request_id == run.run_id and zero model calls. All stores are pinned to
    tmp — the real handle() runs, the real receipt decorator writes."""
    _open_operator(monkeypatch)
    log = tmp_path / "audit.jsonl"
    monkeypatch.setattr(settings, "audit_log_path", str(log))
    # agents.db (unknown-agent lookup) and the container's spine live under tmp
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "data" / "msb.db"))
    monkeypatch.setattr(settings, "decision_spine_db_path", str(tmp_path / "spine.db"))

    response = _client().post(
        "/openbot/run",
        json={"bot_id": "b1", "message": "hello", "agent_id": "no-such-agent-xyz"},
        headers=_auth("tok"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["bot_id"] == "b1"
    run = body["run"]
    assert run["verdict"] == "ERROR"
    assert "unknown agent" in run["error"]
    assert run["run_id"]  # a real run id, not an empty string

    receipts = _lines(log)
    assert len(receipts) == 1
    assert receipts[0]["request_id"] == run["run_id"]
    assert receipts[0]["model_calls"] == 0
    assert receipts[0]["execution_result"]["verdict"] == "ERROR"


def test_run_forwards_governed_params(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The adapter forwards the bounded envelope into the governed handle
    path — tenant/session/approve/privacy/agent_id/repo — and returns the run
    document under bot_id."""
    _open_operator(monkeypatch)
    monkeypatch.setattr(settings, "decision_spine_db_path", str(tmp_path / "spine.db"))
    seen: dict = {}

    async def _stub_handle(request, **kwargs):
        from msb_v3.agent.handle import HandleResult

        seen.update(kwargs)
        return HandleResult(ok=True, run_id="run-42", verdict="PASS", deterministic_hash="h")

    import msb_v3.integrations.openbot as openbot_mod

    monkeypatch.setattr(openbot_mod, "handle", _stub_handle)

    response = _client().post(
        "/openbot/run",
        json={
            "bot_id": "b1",
            "message": "do the thing",
            "session_id": "s1",
            "tenant": "acme",
            "privacy": False,
            "approve": True,
            "repo": "/tmp/x",
        },
        headers=_auth("tok"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["bot_id"] == "b1"
    assert body["run"]["run_id"] == "run-42"
    assert body["run"]["verdict"] == "PASS"
    assert seen["tenant"] == "acme"
    assert seen["session"] == "s1"
    assert seen["privacy"] is False
    assert seen["approve"] is True
    assert seen["repo"] == "/tmp/x"
    assert seen["agent_id"] is None
