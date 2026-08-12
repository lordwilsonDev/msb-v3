"""The Cockpit — one read-only screen over the whole living system.

Serves a single self-contained page at /cockpit (inline CSS/JS, no CDN, no
build step) plus two JSON surfaces:

- /cockpit/api  — aggregated state for every panel: services, mission,
  governance brakes, flywheel, hygiene gate, audit chain, vault/RAG
  freshness, research runs, memory, rate-limit rejections, recent errors.
  Probes are parallel and bounded (asyncio.gather + short timeouts — the
  home.py lesson) and every panel is error-contained, so one dead service
  costs one panel, never the page.
- /cockpit/find — the find-box: vault semantic search (/rag/search),
  audit-chain text match, and research-run titles, grouped.

Read-only by construction: this module exposes no control actions. All
paths derive from settings.msb_home (portability gate stays green).
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse

from msb_v3.core.config import settings

router = APIRouter(tags=["cockpit"])

# Loopback-pinned self-fetches (same rule home.py learned through the Open
# WebUI proxy bug): never settings.host, which may be a wildcard bind.
_MSB_BASE = f"http://127.0.0.1:{settings.port}"
_PROBE_TIMEOUT_S = 4.0

_RUNTIME_DIR = Path(settings.msb_home) / "runtime"
_RESEARCH_DIR = _RUNTIME_DIR / "research"
_LOG_FILE = Path(settings.msb_home) / "logs" / "server.log"
_HYGIENE_FILE = Path(settings.msb_home) / "artifacts" / "hygiene" / "hygiene_aggregate.json"
_MULCH_DB = _RUNTIME_DIR / "triumvirate" / "mulch_learnings.db"

_VAULT_TENANT = "wilson-vault"


def _safe(fn):
    """Run an in-process read; on any failure return an error panel instead
    of letting one broken surface 500 the whole aggregation."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 — containment boundary
        return {"error": f"{type(exc).__name__}: {exc}"}


async def _probe_json(
    client: httpx.AsyncClient, path: str, *, method: str = "GET", json_body: Optional[dict] = None
) -> Any:
    """One bounded self-probe. Failures and non-JSON responses become error
    dicts — never exceptions that kill the gather."""
    try:
        r = await client.request(method, _MSB_BASE + path, json=json_body, timeout=_PROBE_TIMEOUT_S)
        if r.status_code != 200:
            return {"error": f"HTTP {r.status_code}"}
        try:
            return r.json()
        except Exception:
            return {"error": "INVALID (non-JSON)"}
    except Exception as exc:  # noqa: BLE001 — containment boundary
        return {"error": type(exc).__name__}


# --- in-process readers ---------------------------------------------------

def _hygiene_state() -> Dict[str, Any]:
    try:
        data = json.loads(_HYGIENE_FILE.read_text())
    except FileNotFoundError:
        return {"error": "no aggregate yet (run make hygiene)"}
    results = data.get("results", [])
    # The aggregate file has no top-level verdict field — it is derived: pass
    # only when every experiment passed (an empty run is "unknown", never
    # "pass" — the gate must not bless a run that didn't happen).
    if results:
        aggregate = "pass" if all(r.get("verdict") == "pass" for r in results) else "fail"
    else:
        aggregate = "unknown"
    return {
        "timestamp": data.get("timestamp"),
        "aggregate": aggregate,
        "experiments": len(results),
        "results": [{"experiment": r.get("experiment"), "verdict": r.get("verdict")} for r in results],
    }


def _audit_state() -> Dict[str, Any]:
    from msb_v3.uac.audit_chain import AuditChain

    chain = AuditChain()
    verify = chain.verify_chain()
    recent = chain.get_chain()[-8:]
    return {
        "valid": verify.get("valid"),
        "record_count": verify.get("record_count", 0),
        "recent": [
            {
                "seq": r.seq,
                "component": r.component,
                "event_type": r.event_type,
                "timestamp": r.timestamp,
            }
            for r in recent
        ],
    }


def _governance_state() -> Dict[str, Any]:
    from msb_v3.governance.approval import ApprovalQueue
    from msb_v3.governance.budget import BudgetLedger
    from msb_v3.governance.governor import OuroborosGovernor
    from msb_v3.governance.killswitch import KillSwitch

    return {
        "killswitch": KillSwitch().state(),
        "budgets": BudgetLedger.from_settings().state(),
        "approvals_pending": len(ApprovalQueue().pending()),
        "governor_history": len(OuroborosGovernor.from_settings().history()),
    }


def _research_runs() -> Dict[str, Any]:
    if not _RESEARCH_DIR.exists():
        return {"runs": []}
    entries = []
    for p in _RESEARCH_DIR.iterdir():
        if p.is_dir():
            mtime = p.stat().st_mtime
            entries.append({"slug": p.name, "mtime": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()})
    entries.sort(key=lambda e: e["mtime"], reverse=True)
    return {"runs": entries[:15]}


def _recent_errors() -> Dict[str, Any]:
    if not _LOG_FILE.exists():
        return {"count": 0, "lines": []}
    lines = _LOG_FILE.read_text(errors="replace").splitlines()
    hits = [ln for ln in lines if any(k in ln.lower() for k in ("error", "traceback", "exception", "critical"))]
    return {"count": len(hits), "lines": hits[-5:]}


def _mission_state() -> Dict[str, Any]:
    """Mission anchor + Argus mulch tail (both contained; the mulch DB may
    legitimately be absent)."""
    mission: Dict[str, Any] = {}
    try:
        from msb_v3.triumvirate.mission_anchor import MissionAnchor

        anchor = MissionAnchor()
        status = anchor.read()
        verify = anchor.verify()
        mission = {
            "goal": status.get("goal"),
            "phase": status.get("current_phase"),
            "valid": verify.get("valid", False),
            "scope_hash": verify.get("scope_hash"),
            "iteration_count": status.get("iteration_count", 0),
        }
    except Exception as exc:  # noqa: BLE001 — containment boundary
        mission = {"error": f"{type(exc).__name__}: {exc}"}

    argus: List[Dict[str, Any]] = []
    try:
        with sqlite3.connect(_MULCH_DB) as conn:
            rows = conn.execute(
                "SELECT id, timestamp, component, finding_type, description, resolution_status"
                " FROM mulch_learnings ORDER BY timestamp DESC LIMIT 5"
            ).fetchall()
        argus = [
            {
                "id": r[0], "ts": r[1], "component": r[2],
                "finding_type": r[3], "description": r[4], "resolution_status": r[5],
            }
            for r in rows
        ]
    except Exception:  # noqa: BLE001 — containment boundary
        pass
    return {"mission": mission, "argus": argus}


def _rate_limits_state() -> Dict[str, Any]:
    """Live rejection counts from the shared rate/batch guards (the /v1
    chat + embeddings limiters). In-process read of the Prometheus counter
    — the /v1 handlers and this cockpit run in the same server process, so
    the registry is exact in the current single-worker deployment (same
    caveat rate_limit.py documents). Zero-state is honest: no samples ->
    total 0, no counters. The configured caps come from guard_config() —
    the same builder /system/config and the CLIs serve, so this panel
    cannot drift from the other three surfaces."""
    from msb_v3.core.guard_config import guard_config
    from msb_v3.observability.metrics import RATE_LIMIT_REJECTIONS

    counters: List[Dict[str, Any]] = []
    total = 0
    for metric in RATE_LIMIT_REJECTIONS.collect():
        for sample in metric.samples:
            # prometheus-client emits a companion "<name>_created" sample
            # whose value is the creation *timestamp*, not a count — skip it
            # or the panel would show epoch times as rejections.
            if sample.name.endswith("_created"):
                continue
            count = int(sample.value)
            total += count
            counters.append(
                {
                    "limiter": sample.labels.get("limiter", "?"),
                    "reason": sample.labels.get("reason", "?"),
                    "count": count,
                }
            )
    counters.sort(key=lambda c: (c["limiter"], c["reason"]))
    rl = guard_config()["rate_limits"]
    return {
        "total": total,
        "counters": counters,
        "caps": {
            "chat_per_window": rl["OPENAI_CHAT_RATE_MAX"],
            "embeddings_per_window": rl["OPENAI_EMBED_RATE_MAX"],
        },
    }


def _flywheel_state() -> Dict[str, Any]:
    from msb_v3.flywheel.engine import FlywheelEngine

    turns = FlywheelEngine().list()
    newest = turns[0] if turns else None
    problem = newest.problem if newest else None
    if problem and len(problem) > 80:
        problem = problem[:80] + "…"
    return {
        "turn_count": len(turns),
        "newest_status": newest.status if newest else None,
        "newest_stage": newest.stage if newest else None,
        "newest_problem": problem,
        "waiting_approval": sum(1 for t in turns if t.status == "WAITING_APPROVAL"),
    }


def _vault_state() -> Dict[str, Any]:
    try:
        from qdrant_client import QdrantClient
    except Exception as exc:  # noqa: BLE001
        return {"error": f"qdrant client unavailable: {type(exc).__name__}"}
    try:
        host = "localhost"
        client = QdrantClient(host=host, port=6333, prefer_grpc=False)
        collections = [c.name for c in client.get_collections().collections]
        # Same normalization rag.py uses (_collection): tenant wilson-vault
        # lives in the Qdrant collection named tenant_wilson-vault.
        collection = "tenant_" + _VAULT_TENANT
        points = 0
        try:
            points = client.count(collection).count
        except Exception:  # noqa: BLE001 — collection may not exist
            pass
        return {"collections": collections, "vault_points": points}
    except Exception as exc:  # noqa: BLE001 — containment boundary
        return {"error": f"{type(exc).__name__}: {exc}"}


# --- routes ---------------------------------------------------------------

@router.get("/cockpit/api")
async def cockpit_api() -> dict:
    """Aggregated state for every panel. Each probe is bounded (4s) and
    error-contained; the whole aggregation never takes longer than the
    slowest single probe."""
    async with httpx.AsyncClient() as client:
        status, ready, models, active, latest, mem_summary, mem_latest = await asyncio.gather(
            _probe_json(client, "/status"),
            _probe_json(client, "/ready"),
            _probe_json(client, "/models/"),
            _probe_json(client, "/research/assistant/runs/_active"),
            _probe_json(client, "/research/assistant/latest"),
            _probe_json(client, "/evolution/memory/summary"),
            _probe_json(client, "/evolution/memory/latest"),
        )
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "services": {"status": status, "ready": ready, "models": models},
        "research": {
            **{"active": active, "latest": latest},
            **_safe(_research_runs),
        },
        "memory": {"summary": mem_summary, "latest": mem_latest},
        "mission": _safe(_mission_state),
        "flywheel": _safe(_flywheel_state),
        "governance": _safe(_governance_state),
        "limits": _safe(_rate_limits_state),
        "hygiene": _safe(_hygiene_state),
        "audit": _safe(_audit_state),
        "vault": _safe(_vault_state),
        "errors": _safe(_recent_errors),
    }


def _audit_search(query: str) -> List[Dict[str, Any]]:
    from msb_v3.uac.audit_chain import AuditChain

    q = query.lower()
    hits = []
    for r in AuditChain().get_chain():
        needle = " ".join([r.component, r.event_type, json.dumps(r.payload, ensure_ascii=False)])
        if q in needle.lower():
            hits.append(
                {"seq": r.seq, "component": r.component, "event_type": r.event_type, "timestamp": r.timestamp}
            )
        if len(hits) >= 10:
            break
    return hits


def _research_search(query: str) -> List[Dict[str, Any]]:
    q = query.lower()
    hits = []
    try:
        if _RESEARCH_DIR.exists():
            for p in sorted(_RESEARCH_DIR.iterdir()):
                if p.is_dir() and q in p.name.lower():
                    hits.append({"slug": p.name})
    except Exception:  # noqa: BLE001 — containment boundary
        return []
    return hits[:10]


@router.get("/cockpit/find")
async def cockpit_find(q: str = Query("", max_length=200)) -> dict:
    """The find-box: grouped results from vault semantic search, the audit
    chain, and research-run titles. Empty query -> empty groups, 200."""
    query = q.strip()
    result: Dict[str, Any] = {"query": query, "vault": [], "audit": [], "research": []}
    if not query:
        return result

    vault: List[Dict[str, Any]] = []
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                _MSB_BASE + "/rag/search",
                json={"tenant_id": _VAULT_TENANT, "query": query, "limit": 5},
                timeout=_PROBE_TIMEOUT_S,
            )
            if resp.status_code == 200:
                vault = resp.json().get("results", [])
    except Exception:  # noqa: BLE001 — containment boundary
        vault = []
    result["vault"] = vault
    audit = _safe(lambda: _audit_search(query))
    result["audit"] = audit if isinstance(audit, list) else []
    result["research"] = _research_search(query)
    return result


@router.get("/cockpit", response_class=HTMLResponse, include_in_schema=False)
async def cockpit_page() -> HTMLResponse:
    return HTMLResponse(content=_PAGE)


# --- the page -------------------------------------------------------------

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>MSB Cockpit</title>
<style>
:root{
  --bg:#0a0e14; --panel:rgba(255,255,255,.035); --panel-hover:rgba(255,255,255,.06);
  --border:rgba(255,255,255,.08); --teal:#66fcf1; --green:#45a29e; --red:#ff2e63;
  --amber:#f5a623; --text:#c5c6c7; --muted:#8a8d93; --mono:ui-monospace,SFMono-Regular,Menlo,monospace;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  padding:24px;min-height:100vh;background-image:radial-gradient(1200px 600px at 80% -10%,rgba(102,252,241,.06),transparent 60%)}
.wrap{max-width:1400px;margin:0 auto}
header{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-bottom:18px}
h1{font-weight:300;letter-spacing:.14em;font-size:1.35rem;color:var(--teal)}
.chip{font-size:.72rem;color:var(--muted);border:1px solid var(--border);border-radius:20px;padding:3px 10px;font-family:var(--mono)}
.dot{width:10px;height:10px;border-radius:50%;background:var(--muted);transition:background .3s}
.dot.ok{background:var(--green);box-shadow:0 0 10px rgba(69,162,158,.7);animation:pulse 2.4s infinite}
.dot.bad{background:var(--red);box-shadow:0 0 10px rgba(255,46,99,.7)}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.45}}
.spacer{flex:1}
button{background:rgba(255,255,255,.05);color:var(--text);border:1px solid var(--border);border-radius:8px;
  padding:6px 12px;font-size:.8rem;cursor:pointer;transition:all .2s}
button:hover{border-color:var(--teal);color:var(--teal)}
button.active{border-color:var(--green);color:var(--green)}
#updated{font-size:.75rem;color:var(--muted);font-family:var(--mono)}
.focus{border:1px solid var(--border);border-radius:12px;padding:12px 16px;margin-bottom:16px;
  display:flex;gap:10px;align-items:center;font-size:.9rem;background:var(--panel);backdrop-filter:blur(8px)}
.focus.ok{border-color:rgba(69,162,158,.4)}
.focus.info{border-color:rgba(102,252,241,.4)}
.focus.warn{border-color:rgba(255,46,99,.5)}
.focus .tag{font-size:.7rem;letter-spacing:.1em;padding:2px 8px;border-radius:10px;white-space:nowrap}
.focus.ok .tag{background:rgba(69,162,158,.15);color:var(--green)}
.focus.info .tag{background:rgba(102,252,241,.12);color:var(--teal)}
.focus.warn .tag{background:rgba(255,46,99,.15);color:var(--red)}
.find{display:flex;gap:10px;margin-bottom:18px}
.find input{flex:1;background:rgba(255,255,255,.04);border:1px solid var(--border);border-radius:8px;color:var(--text);
  padding:10px 14px;font-size:.95rem;outline:none;transition:border .2s}
.find input:focus{border-color:var(--teal)}
#findResults{margin-bottom:18px;display:none}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:14px}
.card{background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:14px 16px;
  backdrop-filter:blur(8px);transition:transform .18s,border-color .18s,background .18s}
.card:hover{transform:translateY(-2px);border-color:rgba(102,252,241,.35);background:var(--panel-hover)}
.card h2{font-size:.72rem;font-weight:600;letter-spacing:.12em;color:var(--muted);margin-bottom:10px;
  display:flex;justify-content:space-between;align-items:center}
.card h2 .pill{font-size:.68rem;letter-spacing:.04em}
.pill.ok{color:var(--green)} .pill.bad{color:var(--red)} .pill.warn{color:var(--amber)}
.row{display:flex;justify-content:space-between;gap:8px;padding:5px 0;font-size:.85rem;border-bottom:1px solid rgba(255,255,255,.04)}
.row:last-child{border-bottom:none}
.row .k{color:var(--muted)} .row .v{font-family:var(--mono);font-size:.8rem;text-align:right;word-break:break-all}
.bar{height:6px;border-radius:4px;background:rgba(255,255,255,.07);margin:4px 0 10px;overflow:hidden}
.bar i{display:block;height:100%;border-radius:4px;background:var(--green);transition:width .5s}
.bar i.hot{background:var(--amber)} .bar i.full{background:var(--red)}
.verdicts{display:flex;flex-wrap:wrap;gap:5px;margin-top:8px}
.vchip{font-family:var(--mono);font-size:.68rem;padding:2px 7px;border-radius:10px;border:1px solid var(--border)}
.vchip.pass{color:var(--green);border-color:rgba(69,162,158,.4)}
.vchip.fail{color:var(--red);border-color:rgba(255,46,99,.5)}
.err{color:var(--red);font-size:.8rem;font-family:var(--mono)}
.ev{font-family:var(--mono);font-size:.74rem;color:var(--muted);padding:3px 0;border-bottom:1px dashed rgba(255,255,255,.05)}
.ev b{color:var(--teal);font-weight:500}
.skel{height:12px;border-radius:6px;margin:8px 0;background:linear-gradient(90deg,rgba(255,255,255,.04),rgba(255,255,255,.09),rgba(255,255,255,.04));background-size:200% 100%;animation:shimmer 1.2s infinite}
@keyframes shimmer{0%{background-position:200% 0}100%{background-position:-200% 0}}
.group-title{font-size:.75rem;letter-spacing:.12em;color:var(--muted);margin:14px 0 6px}
.fr{border:1px solid var(--border);border-radius:8px;padding:8px 12px;margin-bottom:6px;font-size:.82rem;background:var(--panel)}
.fr .src{color:var(--muted);font-family:var(--mono);font-size:.7rem}
.fr .sc{color:var(--teal);font-family:var(--mono);font-size:.7rem}
.empty{color:var(--muted);font-size:.82rem;font-style:italic}
footer{margin-top:22px;color:var(--muted);font-size:.72rem;text-align:center}
a{color:var(--teal);text-decoration:none}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <span class="dot" id="liveDot"></span>
    <h1>MSB COCKPIT</h1>
    <span class="chip" id="version">v?</span>
    <span class="chip" id="model">model?</span>
    <span class="spacer"></span>
    <span id="updated">—</span>
    <button id="autoBtn" class="active" title="toggle auto-refresh">auto:15s</button>
    <button id="refreshBtn" title="refresh now">refresh</button>
  </header>

  <div class="focus ok" id="focus"><span class="tag">STATUS</span><span id="focusText">loading…</span></div>

  <div class="find">
    <input id="findInput" type="search" placeholder="Find anything — vault, audit chain, research runs…" />
    <button id="findBtn">search</button>
  </div>
  <div id="findResults"></div>

  <div class="grid" id="grid">
    <div class="card" data-panel="services"><h2>SERVICES <span class="pill" data-pill></span></h2><div class="body"><div class="skel"></div><div class="skel"></div><div class="skel"></div></div></div>
    <div class="card" data-panel="mission"><h2>MISSION</h2><div class="body"><div class="skel"></div><div class="skel"></div></div></div>
    <div class="card" data-panel="governance"><h2>GOVERNANCE BRAKES <span class="pill" data-pill></span></h2><div class="body"><div class="skel"></div><div class="skel"></div><div class="skel"></div></div></div>
    <div class="card" data-panel="limits"><h2>RATE LIMIT REJECTIONS <span class="pill" data-pill></span></h2><div class="body"><div class="skel"></div><div class="skel"></div></div></div>
    <div class="card" data-panel="flywheel"><h2>FLYWHEEL <span class="pill" data-pill></span></h2><div class="body"><div class="skel"></div><div class="skel"></div><div class="skel"></div></div></div>
    <div class="card" data-panel="hygiene"><h2>HYGIENE GATE <span class="pill" data-pill></span></h2><div class="body"><div class="skel"></div><div class="skel"></div></div></div>
    <div class="card" data-panel="audit"><h2>AUDIT CHAIN <span class="pill" data-pill></span></h2><div class="body"><div class="skel"></div><div class="skel"></div></div></div>
    <div class="card" data-panel="vault"><h2>VAULT / RAG <span class="pill" data-pill></span></h2><div class="body"><div class="skel"></div><div class="skel"></div></div></div>
    <div class="card" data-panel="research"><h2>RESEARCH RUNS</h2><div class="body"><div class="skel"></div><div class="skel"></div></div></div>
    <div class="card" data-panel="memory"><h2>MEMORY</h2><div class="body"><div class="skel"></div><div class="skel"></div></div></div>
    <div class="card" data-panel="errors"><h2>RECENT ERRORS <span class="pill" data-pill></span></h2><div class="body"><div class="skel"></div><div class="skel"></div></div></div>
  </div>

  <footer>read-only cockpit · data via /cockpit/api · control actions live on the API/CLI</footer>
</div>

<script>
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let auto = true;

function pill(el, cls, txt){ el.className = 'pill ' + cls; el.textContent = txt; }
function bodyFor(panel){ return document.querySelector('[data-panel="'+panel+'"] .body'); }
function panelErr(body, d){ body.innerHTML = '<span class="err">✕ ' + esc(d.error) + '</span>'; }
function rows(body, pairs){
  body.innerHTML = pairs.map(([k,v]) => '<div class="row"><span class="k">'+esc(k)+'</span><span class="v">'+esc(v)+'</span></div>').join('');
}

function renderServices(d){
  const b = bodyFor('services'), p = document.querySelector('[data-panel="services"] [data-pill]');
  const status = d.status || {}, ready = d.ready || {};
  const http = d.status && !d.status.error ? 'ok' : 'bad';
  const readyC = ready.components || {};
  const okCount = Object.values(readyC).filter(v => v === 'ok').length;
  pill(p, http === 'ok' ? 'ok' : 'bad', http === 'ok' ? 'UP' : 'DOWN');
  const models = Array.isArray(d.models) ? d.models : [];
  const active = models.find(m => m.active) || models[0] || {};
  rows(b, [
    ['http', http === 'ok' ? '200' : (d.status && d.status.error || 'ERR')],
    ['ready', ready.ready === true ? 'ready ('+okCount+'/'+Object.keys(readyC).length+')' : 'NOT READY'],
    ['model', active.name ? active.name.split('/').pop() : '—'],
    ['version', status.version || '—'],
  ]);
  const v = $('#version'); if (status.version) v.textContent = 'v' + status.version;
  const m = $('#model'); if (active.name) m.textContent = active.name.split('/').pop();
  $('#liveDot').className = 'dot ' + (http === 'ok' ? 'ok' : 'bad');
}

function renderMission(d){
  const b = bodyFor('mission');
  if (d.error) return panelErr(b, d);
  const m = d.mission || {};
  if (m.error) return panelErr(b, m);
  const rowsHtml = [
    ['goal', m.goal || '—'], ['phase', m.phase || '—'],
    ['anchor valid', m.valid === true ? 'yes' : 'NO'], ['iterations', m.iteration_count ?? '—'],
  ].map(([k,v]) => '<div class="row"><span class="k">'+esc(k)+'</span><span class="v">'+esc(v)+'</span></div>').join('');
  const argus = (d.argus || []).map(a =>
    '<div class="ev"><b>#'+esc(a.id)+'</b> '+esc(a.finding_type)+' · '+esc((a.description||'').slice(0,60))+' · <span style="color:'+(a.resolution_status==='resolved'?'var(--green)':'var(--amber)')+'">'+esc(a.resolution_status||'open')+'</span></div>').join('');
  b.innerHTML = rowsHtml + (argus ? '<div class="group-title">ARGUS</div>' + argus : '<div class="empty">no argus findings</div>');
}

function renderLimits(d){
  const b = bodyFor('limits'), p = document.querySelector('[data-panel="limits"] [data-pill]');
  if (d.error) return panelErr(b, d);
  const total = d.total || 0;
  pill(p, total === 0 ? 'ok' : 'warn', total === 0 ? 'CLEAR' : total + ' rejected');
  const caps = d.caps || {};
  let html = '<div class="row"><span class="k">total rejections</span><span class="v">' + total + '</span></div>';
  const counters = d.counters || [];
  if (counters.length === 0) {
    html += '<div class="empty">no rejections recorded</div>';
  } else {
    html += counters.map(c =>
      '<div class="row"><span class="k">' + esc(c.limiter) + ' · ' + esc(c.reason) + '</span><span class="v">' + esc(c.count) + '</span></div>').join('');
  }
  if (caps.chat_per_window !== undefined && caps.embeddings_per_window !== undefined) {
    html += '<div class="ev">caps: chat ' + esc(caps.chat_per_window) + '/window · embed ' + esc(caps.embeddings_per_window) + '/window</div>';
  }
  b.innerHTML = html;
}

function renderFlywheel(d){
  const b = bodyFor('flywheel'), p = document.querySelector('[data-panel="flywheel"] [data-pill]');
  if (d.error) return panelErr(b, d);
  const waiting = d.waiting_approval || 0;
  const cls = (d.newest_status === 'DONE') ? 'ok' : (waiting ? 'warn' : 'ok');
  pill(p, cls, d.newest_status || '—');
  rows(b, [
    ['turns', d.turn_count ?? 0],
    ['newest', d.newest_status || '—'],
    ['stage', d.newest_stage || '—'],
    ['waiting approval', waiting],
  ]);
  if (d.newest_problem) b.innerHTML += '<div class="ev">' + esc(d.newest_problem) + '</div>';
}

function renderGovernance(d){
  const b = bodyFor('governance'), p = document.querySelector('[data-panel="governance"] [data-pill]');
  if (d.error) return panelErr(b, d);
  const ks = d.killswitch || {};
  const armed = ks.armed === true;
  pill(p, armed ? 'bad' : 'ok', armed ? 'KILL SWITCH ARMED' : 'disarmed');
  let html = '<div class="row"><span class="k">kill switch</span><span class="v">' + (armed ? 'ARMED' : 'disarmed') +
    (ks.reason ? ' · ' + esc(ks.reason) : '') + '</span></div>';
  html += '<div class="row"><span class="k">approvals pending</span><span class="v">' + esc(d.approvals_pending ?? 0) + '</span></div>';
  html += '<div class="row"><span class="k">governor signals</span><span class="v">' + esc(d.governor_history ?? 0) + '</span></div>';
  const budgets = d.budgets || {};
  for (const cat of ['research_calls','tokens','iterations']) {
    const s = budgets[cat] || {};
    if (s.limit === undefined) continue;
    const pct = s.limit < 0 ? 0 : Math.min(100, Math.round(100 * (s.spent || 0) / s.limit));
    const cls = pct >= 100 ? ' full' : (pct >= 80 ? ' hot' : '');
    const label = s.limit < 0 ? (s.spent + '/unlimited') : (s.spent + '/' + s.limit);
    html += '<div class="row"><span class="k">'+cat+'</span><span class="v">'+label+'</span></div>' +
      '<div class="bar"><i style="width:'+pct+'%" class="'+cls+'"></i></div>';
  }
  b.innerHTML = html;
}

function renderHygiene(d){
  const b = bodyFor('hygiene'), p = document.querySelector('[data-panel="hygiene"] [data-pill]');
  if (d.error) return panelErr(b, d);
  const agg = d.aggregate === 'pass';
  pill(p, agg ? 'ok' : 'bad', agg ? 'PASS' : (d.aggregate || 'unknown'));
  rows(b, [['experiments', d.experiments ?? 0], ['last run', d.timestamp ? d.timestamp.slice(0,19).replace('T',' ') : '—']]);
  const chips = (d.results || []).map(r =>
    '<span class="vchip ' + (r.verdict === 'pass' ? 'pass' : 'fail') + '">' + esc(r.experiment) + '</span>').join('');
  b.innerHTML += '<div class="verdicts">' + (chips || '<span class="empty">no runs</span>') + '</div>';
}

function renderAudit(d){
  const b = bodyFor('audit'), p = document.querySelector('[data-panel="audit"] [data-pill]');
  if (d.error) return panelErr(b, d);
  pill(p, d.valid === true ? 'ok' : 'bad', d.valid === true ? 'VERIFIED' : 'TAMPERED');
  rows(b, [['chain valid', d.valid === true ? 'yes' : 'NO'], ['records', d.record_count ?? 0]]);
  const evs = (d.recent || []).slice().reverse().map(r =>
    '<div class="ev"><b>'+esc(r.event_type)+'</b> · '+esc(r.component)+' · <span style="color:var(--muted)">'+esc((r.timestamp||'').slice(11,19))+'</span></div>').join('');
  b.innerHTML += evs;
}

function renderVault(d){
  const b = bodyFor('vault'), p = document.querySelector('[data-panel="vault"] [data-pill]');
  if (d.error) return panelErr(b, d);
  const colls = d.collections || [];
  const has = colls.includes('tenant_wilson-vault');
  pill(p, has ? 'ok' : 'warn', has ? 'INDEXED' : 'NO VAULT COLLECTION');
  rows(b, [['collections', colls.length], ['tenant_wilson-vault points', d.vault_points ?? 0]]);
}

function renderResearch(d){
  const b = bodyFor('research');
  if (d.error) return panelErr(b, d);
  const active = d.active && d.active.active ? d.active.active : null;
  const runs = (d.runs || []).slice(0, 8);
  let html = '<div class="row"><span class="k">active</span><span class="v">' + esc(active || '—') + '</span></div>';
  html += '<div class="row"><span class="k">latest status</span><span class="v">' + esc((d.latest || {}).status || '—') + '</span></div>';
  html += runs.map(r => '<div class="ev">' + esc(r.slug) + ' <span style="color:var(--muted)">' + esc((r.mtime||'').slice(0,10)) + '</span></div>').join('');
  b.innerHTML = html;
}

function renderMemory(d){
  const b = bodyFor('memory');
  if (d.error) return panelErr(b, d);
  const sum = d.summary || {}, latest = d.latest || {};
  rows(b, [['entries', sum.counts && Object.keys(sum.counts).length ? sum.counts.entries ?? '—' : '—'],
           ['latest entry', latest.entry ? 'yes' : '—']]);
}

function renderErrors(d){
  const b = bodyFor('errors'), p = document.querySelector('[data-panel="errors"] [data-pill]');
  if (d.error) return panelErr(b, d);
  const n = d.count || 0;
  pill(p, n === 0 ? 'ok' : 'warn', n === 0 ? 'CLEAR' : n + ' in log');
  if (n === 0) { b.innerHTML = '<span class="empty">no error lines in the recent log</span>'; return; }
  b.innerHTML = (d.lines || []).map(l =>
    '<div class="ev">' + esc(l.slice(-120)) + '</div>').join('');
}

function renderFocus(d){
  const f = $('#focus');
  const errs = d.errors && !d.errors.error ? (d.errors.count || 0) : 0;
  const active = d.research && d.research.active ? d.research.active.active : null;
  if (errs > 0) {
    f.className = 'focus warn'; f.innerHTML = '<span class="tag">WARNING</span><span>' + errs +
      ' error lines in the server log — see the RECENT ERRORS panel.</span>';
  } else if (active) {
    f.className = 'focus info'; f.innerHTML = '<span class="tag">ACTIVE RUN</span><span>Research run <b>' +
      esc(active) + '</b> is in flight — see RESEARCH RUNS.</span>';
  } else {
    f.className = 'focus ok'; f.innerHTML = '<span class="tag">ALL CLEAR</span><span>All services healthy, no active run, no recent errors.</span>';
  }
}

const RENDERERS = { services: renderServices, mission: renderMission, governance: renderGovernance,
                    limits: renderLimits, flywheel: renderFlywheel, hygiene: renderHygiene, audit: renderAudit,
                    vault: renderVault, research: renderResearch, memory: renderMemory, errors: renderErrors };

async function load(){
  try {
    const r = await fetch('/cockpit/api');
    const d = await r.json();
    $('#updated').textContent = 'updated ' + (d.ts ? d.ts.slice(11,19) + 'Z' : '—');
    for (const [name, fn] of Object.entries(RENDERERS)) fn(d[name] || {});
    renderFocus(d);
  } catch (e) {
    $('#liveDot').className = 'dot bad';
    $('#updated').textContent = 'load failed: ' + e;
  }
}

async function find(){
  const q = $('#findInput').value.trim();
  const box = $('#findResults');
  if (!q) { box.style.display = 'none'; return; }
  box.style.display = 'block';
  box.innerHTML = '<div class="skel"></div><div class="skel"></div>';
  const r = await fetch('/cockpit/find?q=' + encodeURIComponent(q));
  const d = await r.json();
  let html = '<div class="group-title">VAULT (' + d.vault.length + ')</div>';
  html += d.vault.map(v => '<div class="fr"><span class="src">' + esc(v.source || 'vault') + '</span> <span class="sc">' + v.score.toFixed(3) + '</span><br>' + esc((v.text||'').slice(0,140)) + '</div>').join('') || '<div class="empty">no vault hits</div>';
  html += '<div class="group-title">AUDIT CHAIN (' + d.audit.length + ')</div>';
  html += d.audit.map(a => '<div class="fr"><span class="src">#' + esc(a.seq) + '</span> <b>' + esc(a.event_type) + '</b> · ' + esc(a.component) + '</div>').join('') || '<div class="empty">no audit hits</div>';
  html += '<div class="group-title">RESEARCH (' + d.research.length + ')</div>';
  html += d.research.map(r2 => '<div class="fr"><span class="src">' + esc(r2.slug) + '</span></div>').join('') || '<div class="empty">no research hits</div>';
  box.innerHTML = html;
}

document.addEventListener('visibilitychange', () => { if (document.hidden) clearInterval(timer); else if (auto) timer = setInterval(load, 15000); });
$('#refreshBtn').addEventListener('click', load);
$('#autoBtn').addEventListener('click', () => {
  auto = !auto; $('#autoBtn').classList.toggle('active', auto);
  clearInterval(timer);
  if (auto) timer = setInterval(load, 15000);
});
$('#findBtn').addEventListener('click', find);
$('#findInput').addEventListener('keydown', e => { if (e.key === 'Enter') find(); });
let timer = setInterval(load, 15000);
load();
</script>
</body>
</html>
"""
