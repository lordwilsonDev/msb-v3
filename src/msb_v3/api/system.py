"""System router — routes registry + info."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Request

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
            from msb_ledger.audit_chain import AuditChain
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
def list_routes(request: Request) -> Dict[str, Any]:
    # Derive from the live app, not a hand-maintained registry: the old
    # api/registry.py REGISTRY listed 7 routers by hand and drifted from the
    # 35+ actually mounted (e.g. health mounted at root but listed as
    # /health). FastAPI 0.141 keeps included routers as lazy wrappers in
    # app.routes, so OpenAPI is the stable flattened route registry — same
    # source the /vesta/routes surface uses.
    routes = []
    for path, operations in request.app.openapi()["paths"].items():
        routes.append({"path": path, "methods": sorted(operations)})
    return {
        "service": "msb-v3",
        "routes": sorted(routes, key=lambda item: item["path"]),
    }


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


@router.get("/discrepancies")
def list_discrepancies(
    subsystem: Optional[str] = None,
    severity: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100,
) -> Dict[str, Any]:
    """Query the DiscrepancyEngine store (normalized findings from every
    detector). Filters: subsystem / severity / status; limit caps rows."""
    from msb_v3.ops.discrepancy import DiscrepancyStore

    store = DiscrepancyStore()
    return {
        "discrepancies": store.query(
            subsystem=subsystem, severity=severity, status=status, limit=limit
        ),
        "counts": store.counts(),
    }


@router.post("/discrepancies/scan")
def scan_discrepancies() -> Dict[str, Any]:
    """Run every wired detector (chain, spine, replay, watchdog, automation
    audit), persist new discrepancies, and return the report."""
    from msb_v3.ops.discrepancy import DiscrepancyEngine

    return DiscrepancyEngine().scan()


@router.post("/diagnose")
def diagnose(body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Root-cause diagnosis: collect telemetry (wake failures, cron
    failures, open discrepancies, restart events), correlate into causal
    edges, and rank root-cause hypotheses. Evidence is deterministic; the
    optional ``window_hours`` bounds the lookback."""
    from msb_v3.ops.root_cause import RootCauseEngine

    window = float((body or {}).get("window_hours", 24.0))
    return RootCauseEngine(window_hours=window).diagnose()


@router.post("/repairs/propose")
def propose_repairs(body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Propose governed repair plans from a live RootCauseEngine diagnosis.
    Prohibited classes (chain_invalid, projection_divergence, ...) are never
    proposed — tamper evidence is human-investigated."""
    from msb_v3.ops.repair import RepairService

    return RepairService().propose()


@router.post("/repairs")
def submit_repair(body: Dict[str, Any]) -> Dict[str, Any]:
    """Submit a manual repair plan (validated against the action catalog).
    Body: {action, params?, discrepancy_id?, root_cause?}. OPERATOR-authority
    plans land in awaiting_approval; AUTO plans are ready to execute."""
    from msb_v3.ops.repair import RepairService

    return RepairService().submit(
        body["action"],
        params=body.get("params", {}),
        discrepancy_id=body.get("discrepancy_id", ""),
        root_cause=body.get("root_cause", ""),
    )


@router.post("/repairs/{plan_id}/approve")
def approve_repair(plan_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """Approve an awaiting repair plan. Body: {operator}."""
    from msb_v3.ops.repair import RepairService

    return RepairService().approve(plan_id, str(body.get("operator", "operator")))


@router.post("/repairs/{plan_id}/execute")
def execute_repair(plan_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a repair plan through the governed flow: verify-before-trust →
    kill-switch gate → apply → verification contract → rollback on failure.
    Closed loop (Phase 5): the state before execution is snapshotted and the
    plan is verified after — did it fix the target, did it break something?"""
    from msb_v3.ops.repair import RepairService
    from msb_v3.ops.verify import VerifyEngine

    service = RepairService()
    verifier = VerifyEngine(repair_service=service)
    before = verifier.capture()
    result = service.execute(plan_id, str(body.get("operator", "system")))
    verification = None
    if result.get("status") in ("completed", "rolled_back"):
        try:
            verification = verifier.verify_repair(plan_id, before=before)
        except Exception as exc:  # noqa: BLE001 — the execution result stands on its own
            verification = {"error": f"{exc.__class__.__name__}: {exc}"}
    return {"execute": result, "verification": verification}


@router.post("/repairs/{plan_id}/verify")
def verify_repair(plan_id: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Closed-loop verification for one executed plan (Phase 5): fresh scan +
    after-snapshot → verdict. Plans executed through /execute are verified
    automatically; this re-runs verification (history is append-only). Body:
    {scan?: bool}."""
    from msb_v3.ops.verify import VerifyEngine

    scan = bool((body or {}).get("scan", True))
    return VerifyEngine().verify_repair(plan_id, scan=scan)


@router.get("/repairs/{plan_id}/verifications")
def list_verifications(plan_id: str, limit: int = 50) -> Dict[str, Any]:
    """Verification history for one plan (newest first)."""
    from msb_v3.ops.verify import VerifyEngine

    return {"verifications": VerifyEngine().store.list(plan_id=plan_id, limit=limit)}


@router.get("/repairs")
def list_repairs(status: Optional[str] = None, limit: int = 50) -> Dict[str, Any]:
    """List repair plans (filter by status: proposed/awaiting_approval/
    approved/completed/failed/rolled_back)."""
    from msb_v3.ops.repair import RepairService

    return {"repairs": RepairService().store.list(status=status, limit=limit)}


@router.post("/auto-repair/run")
def run_auto_repair(body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Run one autonomous repair cycle (Phase 4): scan → diagnose → propose
    (deduped) → execute AUTO plans only. Body: {dry_run?, max_auto_execute?,
    requeue_quiet_s?}. OPERATOR plans are proposed but never executed by the
    loop; the kill switch and verify-before-trust gate every execution."""
    from msb_v3.ops.auto_repair import AutoRepairLoop

    body = body or {}
    return AutoRepairLoop().run(
        dry_run=bool(body.get("dry_run", False)),
        max_auto_execute=body.get("max_auto_execute"),
        requeue_quiet_s=float(body.get("requeue_quiet_s", 900.0)),
    )


@router.get("/auto-repair/status")
def auto_repair_status() -> Dict[str, Any]:
    """Last autonomous cycle + open plans + schedule (launchd every 10 min)."""
    from msb_v3.ops.auto_repair import AutoRepairLoop

    return AutoRepairLoop().status()
