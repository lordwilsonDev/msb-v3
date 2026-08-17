"""Governed-loop console (/console) — run and inspect the canonical path.

The cockpit (/cockpit) shows SYSTEM STATUS. This page is the other half of
the operator surface: it RUNS the canonical governed path and lets you
inspect what happened — intent, task DAG, ActionGate verdicts, verification,
replay reconstruction, and the deterministic hash — in one place.

Security model (important, not incidental):

  * The page is a CLIENT of the existing operator-gated API, not a bypass.
    It holds no secret itself: the operator enters ``MSB_OPERATOR_TOKEN``
    once, it lives in ``sessionStorage`` (never written back to the
    server, never in HTML), and every request carries it as the same
    ``Authorization: Bearer`` header ``curl`` would send. The server-side
    ``require_operator`` gates (fail-closed 503 until configured, 401 on
    mismatch) are the authority — deleting this page changes nothing.
  * The console calls exactly four documented endpoints:
      POST /agent/handle            (run a task, gated)
      GET  /agent/tasks/{id}/replay (event-sourced reconstruction, gated)
      GET  /agent/tasks             (recent runs, gated)
      GET  /metrics/prometheus      (verdict + latency counters, public
                                     scrape endpoint — no token needed)
    All four already exist; this page adds no new route that mutates
    anything. The metrics fetch carries NO bearer header (it is a public
    Prometheus scrape), so the token never leaves the gated calls.

Tests: tests/api/test_console.py — page serves, references the gated
endpoints, contains no token, and renders a fixture run.
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["ui"])

_CONSOLE_HTML = """<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>MSB v3 — Governed Loop Console</title>
<style>
  :root { font-family: system-ui, sans-serif; background:#0b0d12; color:#e6e8eb; }
  body { margin:0; padding:24px; max-width:1100px; }
  h1 { font-size:1.4rem; margin:0 0 4px; }
  .muted { color:#8b95a5; font-size:0.85rem; }
  label { display:block; margin:12px 0 4px; font-weight:600; font-size:0.85rem; }
  textarea, input[type=text], input[type=password], select {
    width:100%; box-sizing:border-box; background:#12161f; color:#e6e8eb;
    border:1px solid #1e2430; border-radius:8px; padding:10px; font:inherit;
  }
  textarea { min-height:72px; }
  .row { display:flex; gap:12px; flex-wrap:wrap; }
  .row > div { flex:1; min-width:200px; }
  button {
    margin-top:14px; background:#1d4ed8; color:#fff; border:none; cursor:pointer;
    padding:10px 18px; border-radius:8px; font-weight:600;
  }
  button:disabled { opacity:.5; cursor:wait; }
  button.ghost { background:#1e2430; color:#e6e8eb; }
  .card { background:#12161f; border:1px solid #1e2430; border-radius:12px; padding:16px; margin-top:16px; }
  .verdict { font-weight:700; padding:2px 10px; border-radius:999px; font-size:0.85rem; }
  .PASS { background:#052e16; color:#4ade80; }
  .FAIL { background:#450a0a; color:#f87171; }
  .ERROR { background:#451a03; color:#fbbf24; }
  pre { background:#0b0d12; border:1px solid #1e2430; border-radius:8px; padding:12px;
        overflow:auto; font-size:0.78rem; line-height:1.5; white-space:pre-wrap; }
  table { border-collapse:collapse; width:100%; font-size:0.82rem; }
  th, td { text-align:left; padding:6px 8px; border-bottom:1px solid #1e2430; vertical-align:top; }
  th { color:#8b95a5; font-weight:600; }
  .ok { color:#4ade80; } .bad { color:#f87171; } .warn { color:#fbbf24; }
  .task-row { cursor:pointer; }
  .task-row:hover { background:#161b26; }
  #status { margin-top:10px; font-size:0.85rem; }
  .metrics-strip { display:flex; gap:8px; flex-wrap:wrap; align-items:center;
                   margin:0 0 12px; font-size:0.82rem; }
  .chip { background:#0b0d12; border:1px solid #1e2430; border-radius:999px;
          padding:3px 10px; display:inline-flex; gap:6px; align-items:center; }
  .chip b { font-variant-numeric:tabular-nums; }
</style>
</head>
<body>
<h1>MSB v3 — Governed Loop Console</h1>
<p class="muted">Runs the canonical path (<code>/agent/handle</code>) and shows intent, task DAG, gate verdicts, verification, replay, and the deterministic hash. The token is held only in this browser session and sent as the standard operator bearer header.</p>

<label for="token">Operator token (<code>MSB_OPERATOR_TOKEN</code>)</label>
<input type="password" id="token" placeholder="enter once — kept in sessionStorage" autocomplete="off" />

<label for="request">Request</label>
<textarea id="request" placeholder="e.g. Search the vault for recent decisions about caching and write a one-page summary note"></textarea>

<div class="row">
  <div>
    <label for="approve">Approve writes</label>
    <select id="approve"><option value="false">false (read-only — REFUSE writes)</option><option value="true">true (pre-authorize declared writes)</option></select>
  </div>
  <div>
    <label for="tenant">Tenant</label>
    <input type="text" id="tenant" value="wilson-vault" />
  </div>
  <div>
    <label for="output_dir">Output dir (blank = server default)</label>
    <input type="text" id="output_dir" placeholder="" />
  </div>
</div>

<button id="run">Run governed task</button>
<button class="ghost" id="refresh-tasks">Refresh recent runs</button>
<div id="status"></div>

<div class="card" id="result-card" hidden>
  <h2 style="margin:0 0 10px;font-size:1rem;">Run result</h2>
  <div id="result"></div>
</div>

<div class="card" id="replay-card" hidden>
  <h2 style="margin:0 0 10px;font-size:1rem;">Replay (event-sourced reconstruction)</h2>
  <div id="replay"></div>
</div>

<div class="card">
  <h2 style="margin:0 0 10px;font-size:1rem;">Recent runs</h2>
  <div class="metrics-strip" id="metrics-strip"><span class="muted">metrics loading …</span></div>
  <div id="tasks"><p class="muted">Enter a token and click “Refresh recent runs”.</p></div>
</div>

<script>
const $ = (id) => document.getElementById(id);
const token = () => $("token").value.trim();
const api = async (path, opts = {}) => {
  const headers = Object.assign({"Authorization": "Bearer " + token()}, opts.headers || {});
  if (opts.body) headers["Content-Type"] = "application/json";
  const r = await fetch(path, Object.assign({}, opts, {headers}));
  if (!r.ok) {
    let detail = "HTTP " + r.status;
    try { const j = await r.json(); detail = j.detail ? JSON.stringify(j.detail) : JSON.stringify(j); } catch (_) {}
    throw new Error(detail);
  }
  return r.json();
};
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
  ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" }[c]));
const fmt = (o) => esc(JSON.stringify(o, null, 2));

// --- Metrics strip: verdict counters + latency quantiles ---
// Fed by the public /metrics/prometheus scrape (no bearer header — it is
// not operator-gated). Parsed here in plain JS so the console needs no
// server-side aggregation endpoint.
const VERDICT_LABELS = {"allowed": "SAFE", "indeterminate": "REVIEW", "denied": "BLOCK", "failed": "FAIL"};

function parsePrometheus(text) {
  // Returns { verdicts: {allowed,indeterminate,denied,failed}, buckets: {le: cum} }
  const verdicts = {allowed: 0, indeterminate: 0, denied: 0, failed: 0};
  const buckets = {};
  for (const line of text.split("\n")) {
    let m = line.match(/^msb_v3_actiongate_decisions_total\{verdict="([^"]+)"\} (\S+)/);
    if (m) verdicts[m[1]] = parseFloat(m[2]);
    // Histogram buckets: cumulative counts per le, aggregated across harnesses.
    m = line.match(/^msb_v3_latency_seconds_bucket\{[^}]*le="([^"]+)"\} (\S+)/);
    if (m) buckets[m[1]] = (buckets[m[1]] || 0) + parseFloat(m[2]);
  }
  return {verdicts, buckets};
}

function quantile(buckets, q) {
  // Estimate a latency quantile from cumulative histogram buckets via linear
  // interpolation within the bucket that crosses the quantile threshold.
  const les = Object.keys(buckets).filter((k) => k !== "+Inf").map(Number).sort((a, b) => a - b);
  const total = buckets["+Inf"] || 0;
  if (!total || !les.length) return null;
  const target = q * total;
  let cum = 0;
  for (const le of les) {
    const b = buckets[String(le)] || 0;
    if (cum + b >= target) {
      const lo = le === les[0] ? 0 : les[les.indexOf(le) - 1];
      const span = b > 0 ? (target - cum) / b : 0;
      return lo + span * (le - lo);
    }
    cum += b;
  }
  return null;
}

function fmtLatency(s) {
  if (s == null) return "—";
  if (s < 0.001) return Math.round(s * 1e6) + "µs";
  if (s < 1) return Math.round(s * 1000) + "ms";
  return s.toFixed(2) + "s";
}

function renderMetricsStrip(data) {
  const {verdicts, buckets} = data;
  const chips = Object.entries(VERDICT_LABELS).map(([k, label]) => {
    const n = verdicts[k] || 0;
    const cls = k === "allowed" ? "ok" : k === "indeterminate" ? "warn" : "bad";
    return `<span class="chip"><span class="${cls}">${label}</span> <b>${n}</b></span>`;
  }).join("");
  const p50 = quantile(buckets, 0.5);
  const p95 = quantile(buckets, 0.95);
  const lat = `<span class="chip"><span class="muted">p50</span> <b>${fmtLatency(p50)}</b></span>` +
    `<span class="chip"><span class="muted">p95</span> <b>${fmtLatency(p95)}</b></span>`;
  return chips + lat;
}

async function loadMetrics() {
  const el = $("metrics-strip");
  try {
    // Public scrape endpoint — deliberately no bearer header (the token must
    // never leave the gated calls). Same-origin, so no CORS concern.
    const r = await fetch("/metrics/prometheus");
    if (!r.ok) throw new Error("HTTP " + r.status);
    el.innerHTML = renderMetricsStrip(parsePrometheus(await r.text()));
  } catch (e) {
    el.innerHTML = `<span class="muted">metrics unavailable: ${esc(e.message)}</span>`;
  }
}

function renderRun(payload) {
  const v = payload.verdict || "ERROR";
  $("result-card").hidden = false;
  let html = `<span class="verdict ${esc(v)}">${esc(v)}</span> `;
  html += `<span class="muted">run_id</span> <code>${esc(payload.run_id || "")}</code> · `;
  html += `<span class="muted">hash</span> <code>${esc(payload.deterministic_hash || "")}</code>`;
  if (payload.error) html += `<p class="bad">${esc(payload.error)}</p>`;
  $("result").innerHTML = html + `<pre>${fmt(payload.trace || {})}</pre>`;
}

function renderReplay(data) {
  $("replay-card").hidden = false;
  let html = "";
  const state = data.derived_state || data.state;
  const consistent = data.consistent;
  if (state) html += `<p>derived state: <b>${esc(state)}</b> · ` +
    `consistent: <span class="${consistent === false ? "bad" : "ok"}">${esc(String(consistent))}</span></p>`;
  if (Array.isArray(data.timeline) && data.timeline.length) {
    html += "<table><tr><th>event</th><th>state</th><th>audit seq</th><th>when</th></tr>";
    for (const e of data.timeline) {
      html += `<tr><td>${esc(e.event_type || "")}</td><td>${esc(e.state || "")}</td>` +
        `<td>${esc(e.audit_seq == null ? "" : e.audit_seq)}</td><td>${esc(e.created_at || "")}</td></tr>`;
    }
    html += "</table>";
  }
  if (data.decisions) html += `<p class="muted">decision trail</p><pre>${fmt(data.decisions)}</pre>`;
  if (data.reason) html += `<p class="warn">${esc(data.reason)}</p>`;
  $("replay").innerHTML = html || `<pre>${fmt(data)}</pre>`;
}

function renderTasks(list) {
  const tasks = Array.isArray(list) ? list : [];
  if (!tasks.length) { $("tasks").innerHTML = '<p class="muted">No runs yet.</p>'; return; }
  let html = "<table><tr><th>run</th><th>state</th><th>created</th><th></th></tr>";
  for (const t of tasks) {
    const id = t.task_id || t.run_id || "";
    html += `<tr class="task-row" data-id="${esc(id)}"><td><code>${esc(id)}</code></td>` +
      `<td>${esc(t.state || "")}</td><td>${esc(t.created_at || "")}</td>` +
      `<td><button class="ghost" style="margin:0;padding:4px 10px;" data-load="${esc(id)}">replay</button></td></tr>`;
  }
  html += "</table>";
  $("tasks").innerHTML = html;
  for (const btn of document.querySelectorAll("[data-load]")) {
    btn.onclick = async (ev) => {
      ev.stopPropagation();
      const id = btn.getAttribute("data-load");
      $("status").textContent = "loading replay for " + id + " …";
      try { renderReplay(await api("/agent/tasks/" + encodeURIComponent(id) + "/replay")); $("status").textContent = ""; }
      catch (e) { $("status").textContent = "replay failed: " + e.message; }
    };
  }
}

$("run").onclick = async () => {
  const req = $("request").value.trim();
  if (!token()) { $("status").textContent = "enter the operator token first"; return; }
  if (!req) { $("status").textContent = "enter a request"; return; }
  $("run").disabled = true;
  $("status").textContent = "running governed task … (watch the run; refused writes show as FAIL/BLOCK)";
  try {
    const payload = await api("/agent/handle", {
      method: "POST",
      body: JSON.stringify({
        request: req,
        approve: $("approve").value === "true",
        tenant: $("tenant").value.trim() || "wilson-vault",
        output_dir: $("output_dir").value.trim() || undefined,
      }),
    });
    renderRun(payload);
    $("status").textContent = "";
    if (payload.run_id) {
      try { renderReplay(await api("/agent/tasks/" + encodeURIComponent(payload.run_id) + "/replay")); }
      catch (e) { $("status").textContent = "run done; replay fetch failed: " + e.message; }
    }
    loadTasks();
  } catch (e) {
    $("status").textContent = "run failed: " + e.message;
    $("result-card").hidden = false;
    $("result").innerHTML = `<p class="bad">${esc(e.message)}</p>`;
  } finally {
    $("run").disabled = false;
  }
};

async function loadTasks() {
  if (!token()) return;
  try { renderTasks((await api("/agent/tasks", {params: undefined})).tasks); }
  catch (e) { $("tasks").innerHTML = '<p class="muted">tasks unavailable: ' + esc(e.message) + "</p>"; }
}
$("refresh-tasks").onclick = () => { loadTasks(); loadMetrics(); };

// Keep the token in sessionStorage so a reload of the page does not force a
// re-paste, but never persist it across tabs/browser restarts, and never
// send it anywhere except the same-origin API with the bearer header.
$("token").value = sessionStorage.getItem("msb_console_token") || "";
$("token").addEventListener("input", () =>
  sessionStorage.setItem("msb_console_token", $("token").value.trim()));

// The metrics strip is public (Prometheus scrape) — load it on page load so
// the operator sees verdict/latency health even before entering a token.
loadMetrics();
</script>
</body>
</html>
"""


@router.get("/console", response_class=HTMLResponse)
async def console() -> str:
    return _CONSOLE_HTML
