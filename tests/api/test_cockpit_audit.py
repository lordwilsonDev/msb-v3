"""Cockpit evidence stream — the /cockpit/audit tail + dashboard folding.

Pins the read-only evidence-stream surface over logs/audit.jsonl: it tails
the structured audit log, filters by verdict / MoIE verdict / intent, is
tolerant of a missing file or a corrupt line, and the old /dashboard studio
link page now redirects to the single observability surface (/cockpit).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from msb_v3.api.app import create_app
from msb_v3.core.config import settings


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


def _receipt(request_id: str, verdict: str, moie: str | None, intent: str) -> dict:
    return {
        "request_id": request_id,
        "intent": intent,
        "moie_verdict": moie,
        "execution_result": {"verdict": verdict},
        "model_calls": 0,
        "audit_hash": f"hash-{request_id}",
    }


def _write(path: Path, receipts: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for r in receipts:
            fh.write(json.dumps(r) + "\n")


def test_cockpit_audit_tails_and_filters(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log = tmp_path / "audit.jsonl"
    monkeypatch.setattr(settings, "audit_log_path", str(log))
    _write(
        log,
        [
            _receipt("a", "PASS", "APPROVE", "research the vault"),
            _receipt("b", "BLOCKED", "BLOCK", "rm -rf production"),
            _receipt("c", "FAIL", "APPROVE", "write a brief"),
        ],
    )

    body = client.get("/cockpit/audit").json()
    assert body["total"] == 3
    assert [r["request_id"] for r in body["receipts"]] == ["a", "b", "c"]

    # verdict filter (exact)
    assert [r["request_id"] for r in client.get("/cockpit/audit", params={"verdict": "BLOCKED"}).json()["receipts"]] == ["b"]
    # moie-verdict filter (exact)
    assert [r["request_id"] for r in client.get("/cockpit/audit", params={"moie_verdict": "APPROVE"}).json()["receipts"]] == ["a", "c"]
    # intent substring
    assert [r["request_id"] for r in client.get("/cockpit/audit", params={"intent": "rf"}).json()["receipts"]] == ["b"]
    # limit clamps to the tail
    assert len(client.get("/cockpit/audit", params={"limit": 2}).json()["receipts"]) == 2


def test_cockpit_audit_missing_file_is_empty(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(settings, "audit_log_path", str(tmp_path / "nope.jsonl"))
    r = client.get("/cockpit/audit")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0
    assert body["receipts"] == []


def test_cockpit_audit_skips_corrupt_lines(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log = tmp_path / "audit.jsonl"
    monkeypatch.setattr(settings, "audit_log_path", str(log))
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        "{not json}\n" + json.dumps(_receipt("ok", "PASS", "APPROVE", "x")) + "\n",
        encoding="utf-8",
    )
    body = client.get("/cockpit/audit").json()
    assert body["total"] == 1
    assert body["receipts"][0]["request_id"] == "ok"


def test_dashboard_redirects_to_cockpit(client: TestClient) -> None:
    """The studio link-card page is folded into the cockpit — /dashboard
    redirects to the single observability surface instead of serving a
    duplicate page."""
    r = client.get("/dashboard", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/cockpit"


def test_status_still_served_by_studio(client: TestClient) -> None:
    """Folding /dashboard must not disturb the canonical /status route that
    lives in the same studio router."""
    r = client.get("/status")
    assert r.status_code == 200
    assert r.json()["service"] == "msb-v3"
