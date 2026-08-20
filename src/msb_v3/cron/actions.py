"""Built-in cron actions — the governed work a job can schedule.

Every action is a synchronous ``callable(params) -> dict`` with a result of
the shape ``{"ok": bool, "summary": str, "detail": dict}``. The scheduler
wraps execution with the job's timeout, retries, kill-switch gate, run
history, evidence receipt, and audit-chain record — the action itself only
does its one job.

Actions must be safe to run unattended by construction: bounded input,
bounded output, no interactive prompts. ``http_call`` is restricted to the
configured host allowlist (default: loopback only) and fails closed on
anything else — the same rule as every other outward-facing surface of the
governed runtime.
"""

from __future__ import annotations

import gzip
import json
import logging
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List

from msb_v3.core.config import settings

logger = logging.getLogger(__name__)

ActionFn = Callable[[Dict[str, Any]], Dict[str, Any]]


def _now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _fail(message: str, **detail: Any) -> Dict[str, Any]:
    return {"ok": False, "summary": message, "detail": detail}


def _ok(summary: str, **detail: Any) -> Dict[str, Any]:
    return {"ok": True, "summary": summary, "detail": detail}


# --- health_check ---------------------------------------------------------

def action_health_check(params: Dict[str, Any]) -> Dict[str, Any]:
    """Verify the core loop is alive: SQLite readable, audit chain readable,
    kill switch state readable (fail-closed — unreadable state counts as
    armed, so this reports unhealthy rather than pretending)."""
    from msb_v3.db.sqlite import healthcheck
    from msb_v3.governance.killswitch import KillSwitch

    checks: Dict[str, Any] = {}
    checks["db"] = healthcheck()
    try:
        chain_db = Path(settings.db_path).parent / "uac" / "audit_chain.db"
        if chain_db.exists():
            with sqlite3.connect(str(chain_db)) as conn:
                count = conn.execute("SELECT COUNT(*) FROM audit_records").fetchone()[0]
            checks["audit_chain"] = f"ok ({count} records)"
        else:
            checks["audit_chain"] = f"error: chain db missing ({chain_db.name})"
    except Exception as exc:  # noqa: BLE001 — probe must report, not raise
        checks["audit_chain"] = f"error: {exc}"
    try:
        checks["kill_switch"] = "armed" if KillSwitch().is_armed() else "disarmed"
    except Exception as exc:  # noqa: BLE001
        checks["kill_switch"] = f"error: {exc}"
    ok = checks["db"] == "ok" and not str(checks["audit_chain"]).startswith("error:")
    return _ok("health check passed" if ok else "health check degraded", checks=checks) if ok else _fail(
        "health check degraded", checks=checks
    )


# --- audit_chain_verify ---------------------------------------------------

def action_audit_chain_verify(params: Dict[str, Any]) -> Dict[str, Any]:
    """Verify the hash chain is intact (internal verify + external anchor
    when configured). ``detail`` carries both reports; a broken or
    tampered chain is a FAILED action — the alerting job's whole purpose."""
    from msb_ledger.audit_chain import AuditChain
    from msb_ledger.chain_anchor import ChainAnchor

    chain_db = Path(settings.db_path).parent / "uac" / "audit_chain.db"
    if not chain_db.exists():
        return _fail(f"audit chain db missing: {chain_db}")
    chain = AuditChain(db_path=str(chain_db))
    report: Dict[str, Any] = {"internal": chain.verify_chain()}
    try:
        anchor = ChainAnchor.from_env()
        report["anchored"] = anchor.verify(chain)
    except Exception as exc:  # noqa: BLE001 — no key configured is not a failure
        report["anchored"] = {"valid": False, "reason": f"anchor unavailable: {exc}"}
    internal_ok = bool(report["internal"].get("valid"))
    anchored = report.get("anchored", {})
    anchored_ok = bool(anchored.get("valid")) or (
        # A valid-but-stale anchor is a WATCH item, not a chain failure:
        # newer records were legitimately appended after the last re-anchor.
        anchored.get("stale") is True and anchored.get("reason") is not None
    )
    ok = internal_ok and anchored_ok
    summary = (
        f"chain ok ({report['internal'].get('record_count')} records)"
        if ok
        else f"chain problem: {anchored.get('reason') or report['internal'].get('reason')}"
    )
    return (_ok if ok else _fail)(summary, report=report)


# --- backup_spine ----------------------------------------------------------

def action_backup_spine(params: Dict[str, Any]) -> Dict[str, Any]:
    """Snapshot the only-copy data (SQLite via the online backup API + file
    copies) into the backup root, then prune old backups. Params:
    ``keep`` (default 14), ``destination`` (default MSB_BACKUP_DIR)."""
    from msb_v3.ops.backup import (
        create_backup,
        default_notary_log,
        default_paths,
        prune_backups,
    )

    data_dir, storage_dir, dest_root = default_paths()
    dest = params.get("destination") or dest_root
    keep = int(params.get("keep", 14))
    timestamp = _now_compact()
    manifest = create_backup(
        data_dir, storage_dir, Path(dest), timestamp=timestamp, notary_log=default_notary_log()
    )
    pruned = prune_backups(Path(dest), keep=keep)
    return _ok(
        f"backup {manifest.path} dbs={manifest.db_count} pruned={len(pruned)}",
        path=str(manifest.path),
        db_count=manifest.db_count,
        checksums=len(manifest.checksums),
        pruned=len(pruned),
    )


# --- metric_export ---------------------------------------------------------

def action_metric_export(params: Dict[str, Any]) -> Dict[str, Any]:
    """Snapshot the Prometheus registry (text exposition + JSON summary) to
    a file. Params: ``destination`` (default runtime/exports/metrics-<ts>.txt)
    and ``json`` (bool, default True) to also write the .json summary."""
    from prometheus_client import generate_latest

    dest = Path(params.get("destination") or (Path(settings.msb_home) / "runtime" / "exports" / f"metrics-{_now_compact()}.txt"))
    dest.parent.mkdir(parents=True, exist_ok=True)
    text = generate_latest().decode("utf-8")
    dest.write_text(text, encoding="utf-8")
    written = [str(dest)]
    if bool(params.get("json", True)):
        json_path = dest.with_suffix(".json")
        from msb_v3.observability.metrics import Metrics

        json_path.write_text(
            json.dumps({"ready": Metrics._ready, "exported_at": datetime.now(timezone.utc).isoformat()}, indent=2),
            encoding="utf-8",
        )
        written.append(str(json_path))
    return _ok(f"metrics exported ({len(text)} bytes)", files=written, bytes=len(text))


# --- log_rotation ----------------------------------------------------------

def action_log_rotation(params: Dict[str, Any]) -> Dict[str, Any]:
    """Rotate and archive logs under logs/.

    Safe-by-construction for an in-process scheduler:

    - ``audit.jsonl`` is reopened per append (append_receipt), so a
      rename-based rotation is lossless: once rotated, the next append
      creates a fresh file. Rotated when larger than ``max_bytes``
      (default 20 MB).
    - Other ``logs/*.log`` files may be held open by external processes
      (launchd stdout/stderr). Those are never deleted or truncated —
      instead, files older than ``max_age_days`` (default 7) are archived
      as gzip *snapshots* (the live file is left alone).
    - Archives older than ``keep_days`` (default 30) are pruned.

    Returns the list of files written/removed.
    """
    logs = Path(settings.audit_log_path).parent if params.get("log_dir") is None else Path(str(params["log_dir"]))
    archive = logs / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    max_bytes = int(params.get("max_bytes", 20 * 1024 * 1024))
    max_age_days = int(params.get("max_age_days", 7))
    keep_days = int(params.get("keep_days", 30))
    now = datetime.now(timezone.utc)
    written: List[str] = []
    removed: List[str] = []

    audit = logs / "audit.jsonl"
    if audit.exists() and audit.stat().st_size > max_bytes:
        stamp = _now_compact()
        rotated = archive / f"audit-{stamp}.jsonl"
        shutil.move(str(audit), str(rotated))
        with open(rotated, "rb") as fh_in, gzip.open(f"{rotated}.gz", "wb") as fh_out:
            shutil.copyfileobj(fh_in, fh_out)
        rotated.unlink()
        written.append(f"{rotated}.gz")

    for log in sorted(logs.glob("*.log")):
        try:
            age = now - datetime.fromtimestamp(log.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        if age.days >= max_age_days:
            stamp = _now_compact()
            snap = archive / f"{log.stem}-{stamp}.log.gz"
            with open(log, "rb") as fh_in, gzip.open(snap, "wb") as fh_out:
                shutil.copyfileobj(fh_in, fh_out)
            written.append(str(snap))

    cutoff = now.timestamp() - keep_days * 86400
    for old in sorted(archive.glob("*.gz")):
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink()
                removed.append(str(old))
        except OSError:
            continue
    return _ok(
        f"log rotation complete: {len(written)} archived, {len(removed)} pruned",
        archived=written,
        pruned=removed,
    )


# --- http_call -------------------------------------------------------------

def _host_allowed(host: str) -> bool:
    """Fail-closed host check against the configured allowlist."""
    allowed = {h.strip().lower() for h in settings.cron_http_hosts.split(",") if h.strip()}
    return host.strip().lower() in allowed


def action_http_call(params: Dict[str, Any]) -> Dict[str, Any]:
    """Call an HTTP endpoint. Fail-closed: the URL's host must be on the
    configured allowlist (MSB_CRON_HTTP_HOSTS, default loopback only) or the
    action refuses. Params: ``url`` (required), ``method`` (default GET),
    ``headers`` (dict), ``json`` (body dict), ``timeout_s`` (default 10)."""
    from urllib.parse import urlparse

    import httpx

    url = params.get("url")
    if not isinstance(url, str) or not url:
        return _fail("http_call requires a url")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return _fail(f"http_call only allows http(s), got scheme {parsed.scheme!r}")
    if not _host_allowed(parsed.hostname or ""):
        return _fail(f"http_call host {parsed.hostname!r} not on allowlist (MSB_CRON_HTTP_HOSTS)")
    method = str(params.get("method", "GET")).upper()
    timeout_s = float(params.get("timeout_s", 10.0))
    headers = params.get("headers")
    if headers is not None and not isinstance(headers, dict):
        return _fail("headers must be a dict")
    body = params.get("json")
    if body is not None and not isinstance(body, dict):
        return _fail("json body must be a dict")
    try:
        with httpx.Client(timeout=timeout_s) as client:
            resp = client.request(method, url, headers=headers, json=body)
        text = resp.text[:2000]
        ok = resp.status_code < 400
        summary = f"HTTP {resp.status_code} {method} {url}" + ("" if ok else f": {text[:200]}")
        return (_ok if ok else _fail)(
            summary,
            status_code=resp.status_code,
            url=url,
            body_preview=text,
        )
    except httpx.HTTPError as exc:
        return _fail(f"http_call failed: {exc.__class__.__name__}: {exc}", url=url)


# --- wake_agent -----------------------------------------------------------

def action_wake_agent(params: Dict[str, Any]) -> Dict[str, Any]:
    """The resident agent's 5-minute wake cycle: process pending wake-inbox
    messages (bounded by MSB_WAKE_MAX_PER_RUN, overridable via params) and
    write responses to the outbox. Runs under the scheduler's kill switch /
    retries / timeout / receipts like every other action. An empty inbox is
    a successful no-op — the loop exists to stay warm, not to churn."""
    from msb_v3.wake.runner import run_wake_cycle

    max_items = params.get("max_items")
    try:
        max_items = int(max_items) if max_items is not None else None
    except (TypeError, ValueError):
        return _fail("wake_agent max_items must be an integer")
    return run_wake_cycle(max_items=max_items)


# --- registry --------------------------------------------------------------

ACTIONS: Dict[str, ActionFn] = {
    "health_check": action_health_check,
    "audit_chain_verify": action_audit_chain_verify,
    "backup_spine": action_backup_spine,
    "metric_export": action_metric_export,
    "log_rotation": action_log_rotation,
    "http_call": action_http_call,
    "wake_agent": action_wake_agent,
}


def run_action(action_type: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Dispatch one action; unknown action types fail closed (the job's run
    is recorded as FAILED — an unknown action is a misconfiguration, not a
    no-op)."""
    fn = ACTIONS.get(action_type)
    if fn is None:
        return _fail(f"unknown cron action type: {action_type!r} (known: {sorted(ACTIONS)})")
    try:
        result = fn(params or {})
    except Exception as exc:  # noqa: BLE001 — action bugs surface as failed runs, not crashes
        logger.exception("cron action %s failed with an exception", action_type)
        return _fail(f"action raised: {exc.__class__.__name__}: {exc}")
    if not isinstance(result, dict) or "ok" not in result:
        return _fail(f"action returned a malformed result: {result!r}")
    return result
