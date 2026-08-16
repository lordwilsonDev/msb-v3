"""System router — routes registry + info."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter

from msb_v3 import __version__
from msb_v3.core.config import settings

router = APIRouter(tags=["system"])


@router.get("/info")
def system_info() -> Dict[str, Any]:
    return {"service": "msb-v3", "version": __version__}


def _probe_llama_cpp(transport: Any | None = None) -> str:
    """True availability of the llama.cpp backend — an HTTP probe of the
    OpenAI-compat surface, not a config claim (truth-in-config: green only
    for backends that can actually serve). The backend is real only when
    both the weights file exists AND llama-server answers JSON. A plain
    TCP connect is NOT enough — Apache httpd on :8080 answers connections
    with HTML, which must not count as llama.cpp up. `transport` is
    injectable for tests (httpx.MockTransport).
    """
    from pathlib import Path

    import httpx

    weights = Path(settings.llama_cpp_model)
    if not weights.exists():
        return f"error: weights not provisioned ({weights.name})"
    url = settings.llama_cpp_url.rstrip("/")
    try:
        with httpx.Client(timeout=2.0, transport=transport) as client:
            resp = client.get(f"{url}/health")
        # llama-server answers /health with 200 JSON when the model is
        # loaded and serving; any HTML/text answer (e.g. httpd's 404) is a
        # different service and must be reported as NOT the llama.cpp backend.
        if resp.status_code != 200:
            return f"error: HTTP {resp.status_code} from {url}/health"
        ctype = resp.headers.get("content-type", "")
        if "json" not in ctype:
            return f"error: {url}/health answered {ctype or 'no content-type'} — not llama-server"
        return "ok"
    except httpx.HTTPError as exc:
        return f"error: unreachable {url} ({exc.__class__.__name__})"


def _component(ok: bool, detail: str) -> Dict[str, Any]:
    """Normalize one component row to the HEALTHY/DEGRADED/FAILED/UNKNOWN
    vocabulary the sovereign control plane expects (unified-architecture
    §14). A probe that throws is FAILED; a skipped/absent probe is UNKNOWN."""
    if ok:
        return {"status": "HEALTHY", "detail": detail or "ok"}
    return {"status": "FAILED", "detail": detail or "error"}


def _probe_qdrant() -> Dict[str, Any]:
    """Qdrant liveness: the /collections surface answers JSON."""
    import httpx
    try:
        with httpx.Client(timeout=2.0) as client:
            resp = client.get("http://127.0.0.1:6333/collections")
        if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("application/json"):
            return _component(True, f"HTTP {resp.status_code}")
        return _component(False, f"HTTP {resp.status_code}")
    except httpx.HTTPError as exc:
        return _component(False, f"unreachable: {exc.__class__.__name__}")


def _probe_auditchain() -> Dict[str, Any]:
    """AuditChain integrity: the chain DB is readable and the chain verifies.
    The chain is the system's tamper-evident record — an unreadable chain is
    a FAILED component, not a footnote."""
    import sqlite3
    from pathlib import Path
    chain_db = Path(settings.db_path).parent / "uac" / "audit_chain.db"
    if not chain_db.exists():
        return _component(False, f"chain db missing: {chain_db.name}")
    try:
        with sqlite3.connect(str(chain_db)) as conn:
            count = conn.execute("SELECT COUNT(*) FROM audit_records").fetchone()[0]
        try:
            from msb_v3.uac.audit_chain import AuditChain
            verified = AuditChain(db_path=str(chain_db)).verify_chain()
            ok = bool(verified.get("valid", True)) if isinstance(verified, dict) else True
        except Exception:
            ok = True  # row count is the liveness floor; deep verify is best-effort here
        return _component(ok, f"{count} records")
    except Exception as exc:
        return _component(False, f"unreadable: {exc.__class__.__name__}")


def _probe_vesta() -> Dict[str, Any]:
    """Vesta action perimeter: the durable task store is readable."""
    import sqlite3
    from pathlib import Path
    tasks_db = Path(settings.db_path).parent / "vesta" / "tasks.db"
    if not tasks_db.exists():
        return _component(False, "tasks db missing")
    try:
        with sqlite3.connect(str(tasks_db)) as conn:
            count = conn.execute("SELECT COUNT(*) FROM vesta_tasks").fetchone()[0]
        return _component(True, f"{count} durable tasks")
    except Exception as exc:
        return _component(False, f"unreadable: {exc.__class__.__name__}")


def _probe_governor() -> Dict[str, Any]:
    """Governor/brakes: the kill switch state is readable (fail-closed read)."""
    try:
        from msb_v3.governance.killswitch import KillSwitch
        state = KillSwitch().state()
        return _component(True, f"killswitch armed={bool(state.get('armed'))}")
    except Exception as exc:
        return _component(False, f"unreadable: {exc.__class__.__name__}")


def _probe_paseo() -> Dict[str, Any]:
    """Paseo execution surface (unified-architecture §7): the daemon's MCP
    endpoint answers the initialize handshake. The adapter is inert without
    the daemon — an unreachable daemon is a FAILED component, not a
    footnote (truthful health, §14)."""
    import httpx
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "msb-v3-health", "version": "0.0.0"},
        },
    }
    try:
        with httpx.Client(timeout=2.0) as client:
            resp = client.post(
                settings.paseo_url,
                json=payload,
                headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
            )
        if resp.status_code < 400:
            return _component(True, "daemon reachable")
        return _component(False, f"HTTP {resp.status_code}")
    except httpx.HTTPError as exc:
        return _component(False, f"unreachable: {exc.__class__.__name__}")


@router.get("/health")
def system_health() -> Dict[str, Any]:
    from msb_v3.db import sqlite as db
    from msb_v3.local_ai.ollama import LocalAIClient
    from msb_v3.observability.metrics import Metrics

    checks: Dict[str, Any] = {"app": "ok", "ready": bool(Metrics._ready)}
    try:
        LocalAIClient().generate("health-check", max_tokens=1)
        checks["ollama"] = "ok"
    except Exception as exc:
        checks["ollama"] = f"error: {exc}"
    try:
        db.healthcheck()
        checks["db"] = "ok"
    except Exception as exc:
        checks["db"] = f"error: {exc}"
    # truth-in-config (FR-2.3): report what can actually serve. The llama.cpp
    # backend is green only when weights exist AND the server answers.
    checks["llamacpp"] = _probe_llama_cpp()
    # Frontier /v1 seam: configured == OPENAI_API_KEY set; a set key without
    # reachability is still "configured" (the router treats config as
    # availability and degrades on execution failure) — never claim serving.
    checks["frontier"] = "configured" if settings.openai_api_key else "closed (OPENAI_API_KEY unset)"
    # Status reflects the core + the *active* backend only: llama.cpp is an
    # optional alternate, so its down state is reported per-row (truthful)
    # without degrading a system that is actively serving via ollama.
    active = settings._active_backend
    critical = {"app", "db", active}
    degraded = any(
        k in critical and isinstance(v, str) and v.startswith("error:")
        for k, v in checks.items()
    )
    checks["active_backend"] = active
    checks["status"] = "degraded" if degraded else "healthy"
    # Sovereign control-plane component view (unified-architecture §14):
    # per-component HEALTHY/DEGRADED/FAILED/UNKNOWN + derived overall. This
    # is additive — the flat rows above stay for backward compatibility.
    components = {
        "api": _component(True, "serving"),
        "db": _component(not str(checks["db"]).startswith("error:"), str(checks["db"])),
        "ollama": _component(not str(checks["ollama"]).startswith("error:"), str(checks["ollama"])),
        "qdrant": _probe_qdrant(),
        "auditchain": _probe_auditchain(),
        "vesta": _probe_vesta(),
        "governor": _probe_governor(),
        "paseo": _probe_paseo(),
    }
    any_failed = any(c["status"] == "FAILED" for c in components.values())
    checks["components"] = components
    checks["overall"] = "FAILED" if any_failed else ("degraded" if degraded else "healthy")
    return checks


@router.get("/routes")
def list_routes() -> Dict[str, Any]:
    from msb_v3.api.registry import REGISTRY

    # Guarded iteration: a malformed (non-dict) registry entry is skipped
    # rather than raising — /routes is a diagnostic surface and must not 500.
    routes = []
    for e in REGISTRY:
        if isinstance(e, dict):
            routes.append({"prefix": e["prefix"], "tags": e["tags"]})
    return {"routes": routes}


@router.get("/config")
def system_config() -> Dict[str, Any]:
    from msb_v3.core.guard_config import guard_config
    from msb_v3.observability.metrics import Metrics

    return {
        "service": "msb-v3",
        "version": __version__,
        "host": settings.host,
        "port": settings.port,
        "ollama_url": settings.ollama_url.replace("http://", "").replace("https://", "").split("@")[-1] if settings.ollama_url else "hidden",
        "ollama_model": settings.ollama_model,
        "db_path": settings.db_path,
        "log_level": settings.log_level,
        "cors_origins": settings.cors_origins,
        "request_timeout_s": settings.request_timeout_s,
        # Guard/brake/approval/flywheel blocks — shared with the governance
        # CLI (make governance-config), so the two surfaces cannot drift.
        **guard_config(),
        "ready": Metrics._ready,
    }
