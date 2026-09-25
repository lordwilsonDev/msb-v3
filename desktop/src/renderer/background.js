/**
 * MSB v3 Desktop - Background section (watch-only).
 *
 * Views of what runs behind the runtime; design:
 * docs/superpowers/specs/2026-09-22-background-windows-design.md. Phase 1
 * ships six views; Machine services (launchd) arrives in phase 2. Only the
 * visible view polls, and polling pauses while the window is hidden.
 * Every call is a read: there are no control actions here.
 *
 * Loaded after poller.js and before app.js; uses app.js's el()/clear() at
 * call time only.
 */

(function () {
  'use strict';

  const STATES = ['ok', 'warn', 'fail', 'unknown'];
  const SUBSYSTEM_VIEW = { cron: 'cron', governance: 'governance', automation: 'automation', wake: 'automation', plei: 'plei' };

  const bg = {
    view: 'overview',
    snapshot: null,
    audit: [],
    cronJobs: [],
    cronHistory: {},
    openJob: null,
    governance: null,
    approvals: [],
    calibrate: null,
    lastGoodAt: null,
    lastError: '',
  };
  let root = null;
  let poller = null;

  // --- loads (each returns the primary { ok } result for back-off) ------

  async function loadOverview() {
    const r = await window.msb.background();
    if (r.ok) bg.snapshot = r.data;
    return r;
  }

  async function loadActivity() {
    const r = await window.msb.auditStream(50);
    if (r.ok) bg.audit = (r.data && r.data.receipts) || [];
    return r;
  }

  async function loadCron() {
    const [jobs, snap] = await Promise.all([window.msb.cronJobs(), window.msb.background()]);
    if (jobs.ok) bg.cronJobs = (jobs.data && jobs.data.jobs) || [];
    if (snap.ok) bg.snapshot = snap.data;
    if (jobs.ok && bg.openJob) await loadHistory(bg.openJob);
    return jobs;
  }

  async function loadHistory(jobId) {
    const h = await window.msb.cronHistory(jobId);
    if (h.ok) bg.cronHistory[jobId] = (h.data && h.data.runs) || [];
  }

  async function loadGovernance() {
    const [gov, ap] = await Promise.all([window.msb.governanceStatus(), window.msb.approvals()]);
    if (gov.ok) bg.governance = gov.data;
    if (ap.ok) bg.approvals = (ap.data && ap.data.items) || [];
    return gov;
  }

  async function loadPlei() {
    const [snap, cal] = await Promise.all([window.msb.background(), window.msb.pleiCalibrate()]);
    if (snap.ok) bg.snapshot = snap.data;
    if (cal.ok) bg.calibrate = cal.data;
    return snap.ok ? cal : snap;
  }

  const VIEWS = [
    { id: 'overview', label: 'Overview', intervalMs: 5000, load: loadOverview, render: renderOverview },
    { id: 'activity', label: 'Activity', intervalMs: 10000, load: loadActivity, render: renderActivity },
    { id: 'cron', label: 'Scheduled jobs', intervalMs: 10000, load: loadCron, render: renderCron },
    { id: 'governance', label: 'Governance', intervalMs: 10000, load: loadGovernance, render: renderGovernance },
    { id: 'automation', label: 'Automation & wake', intervalMs: 10000, load: loadOverview, render: renderAutomation },
    { id: 'plei', label: 'PLEI', intervalMs: 10000, load: loadPlei, render: renderPlei },
  ];

  // --- lifecycle -----------------------------------------------------

  function onResult(r) {
    if (r && r.ok) {
      bg.lastGoodAt = Date.now();
      bg.lastError = '';
    } else {
      bg.lastError = (r && (r.error || r.detail)) || 'read failed';
    }
    draw();
  }

  function startView(id) {
    if (poller) poller.stop();
    const v = VIEWS.find((x) => x.id === id) || VIEWS[0];
    bg.view = v.id;
    poller = window.MsbPoller.createPoller({
      load: v.load,
      intervalMs: v.intervalMs,
      isVisible: () => document.visibilityState === 'visible',
      onResult,
    });
    draw();
    poller.start();
  }

  document.addEventListener('visibilitychange', () => {
    if (poller) poller.wake();
  });

  function mount() {
    if (!root) root = el('div', { class: 'bg' });
    if (!poller) startView(bg.view);
    else draw();
    return root;
  }

  function unmount() {
    if (poller) poller.stop();
    poller = null;
  }

  // --- drawing ---------------------------------------------------------

  function draw() {
    if (!root) return;
    clear(root);
    const side = el('div', { class: 'bg-side' });
    for (const v of VIEWS) {
      side.appendChild(
        el('button', { class: `btn bg-nav${v.id === bg.view ? ' active' : ''}`, text: v.label, onclick: () => startView(v.id) })
      );
    }
    const main = el('div', { class: 'bg-main' });
    main.appendChild(freshness());
    main.appendChild((VIEWS.find((x) => x.id === bg.view) || VIEWS[0]).render());
    root.appendChild(side);
    root.appendChild(main);
  }

  function ago(t) {
    return `${Math.round((Date.now() - t) / 1000)}s ago`;
  }

  function freshness() {
    if (!bg.lastError) {
      return el('div', { class: 'detail', text: bg.lastGoodAt ? `updated ${ago(bg.lastGoodAt)}` : 'loading...' });
    }
    const last = bg.lastGoodAt ? `last good data: ${ago(bg.lastGoodAt)}` : 'no data yet';
    return el('div', { class: 'err', text: `read failed (${bg.lastError}) - ${last}; retrying every 30s` });
  }

  function stateBadge(s) {
    const k = STATES.includes(s) ? s : 'unknown';
    return el('span', { class: `badge st-${k}`, text: k.toUpperCase() });
  }

  function subEntry(name) {
    const subs = (bg.snapshot && bg.snapshot.subsystems) || {};
    return subs[name] || { state: 'unknown', detail: {}, error: 'no snapshot yet' };
  }

  function summarize(name, d) {
    switch (name) {
      case 'cron':
        return `${d.job_count ?? '?'} jobs; failed ${(d.failed || []).length}; overdue ${(d.overdue || []).length}${
          d.scheduler_enabled === false ? '; scheduler OFF' : ''
        }`;
      case 'governance':
        return `kill switch ${d.killswitch_armed ? 'ARMED' : 'off'}; ${d.pending_approvals ?? 0} pending approvals`;
      case 'automation':
        return `${d.dry_run ? 'dry-run' : 'LIVE'}; spent $${d.spent_usd ?? '?'} of $${d.cap_usd ?? '?'}`;
      case 'wake':
        return `${d.enabled ? '' : 'disabled; '}${d.pending ?? '?'} pending; ${d.outbox_count ?? '?'} replies`;
      case 'plei':
        return `${d.predictions ?? '?'} predictions; ${d.pairs ?? '?'} pairs; chain ${d.chain_ok ? 'ok' : 'BROKEN'}`;
      default:
        return '';
    }
  }

  function card(title) {
    const c = el('div', { class: 'card' });
    c.appendChild(el('h2', { text: title }));
    return c;
  }

  function renderOverview() {
    const c = card('Background overview');
    const subs = (bg.snapshot && bg.snapshot.subsystems) || {};
    const names = Object.keys(subs);
    if (!names.length) {
      c.appendChild(el('div', { class: 'empty', text: 'No snapshot yet.' }));
      return c;
    }
    const grid = el('div', { class: 'grid' });
    for (const name of names) {
      const e = subs[name] || {};
      const tile = el('div', { class: 'card bg-tile', onclick: () => startView(SUBSYSTEM_VIEW[name] || 'overview') });
      tile.appendChild(el('h2', { text: name }));
      tile.appendChild(stateBadge(e.state));
      tile.appendChild(el('div', { class: 'detail', text: e.error || summarize(name, e.detail || {}) }));
      grid.appendChild(tile);
    }
    c.appendChild(grid);
    return c;
  }

  /** Receipt intents are structured (``{domain, goals}``). Show the goal text;
   *  never print "[object Object]". */
  function intentText(intent) {
    if (!intent) return '(no intent)';
    if (typeof intent === 'string') return intent.slice(0, 120);
    const parts = [];
    if (intent.domain) parts.push(String(intent.domain));
    if (Array.isArray(intent.goals) && intent.goals.length) parts.push(intent.goals.map(String).join('; '));
    return (parts.join(' - ') || '(no intent detail)').slice(0, 120);
  }

  /** Receipts carry ``timestamps: {decision, execution, verification}``, any of
   *  them nullable; fall back to a flat timestamp key if one is present. */
  function receiptTs(rec) {
    const t = rec.timestamps && typeof rec.timestamps === 'object' ? rec.timestamps : {};
    const first = t.execution || t.decision || t.verification || rec.ts || rec.timestamp || rec.created_at;
    return first ? String(first) : '';
  }

  function renderActivity() {
    const c = card('Activity - governed runs');
    c.appendChild(
      el('div', {
        class: 'detail',
        text: 'Governed-run receipts, newest first. Cron runs are under Scheduled jobs; the full audit-chain feed arrives in phase 3.',
      })
    );
    if (!bg.audit.length) {
      c.appendChild(el('div', { class: 'empty', text: 'No receipts yet.' }));
      return c;
    }
    const list = el('ul', { class: 'list' });
    for (const rec of bg.audit.slice().reverse()) {
      const verdict = (rec.execution_result && rec.execution_result.verdict) || rec.moie_verdict || '?';
      const li = el('li', {});
      li.appendChild(el('span', { class: 'badge kind', text: String(verdict) }));
      li.appendChild(el('strong', { text: ` ${intentText(rec.intent)} ` }));
      li.appendChild(el('span', { class: 'detail', text: receiptTs(rec) }));
      list.appendChild(li);
    }
    c.appendChild(list);
    return c;
  }

  async function toggleJob(jobId) {
    bg.openJob = bg.openJob === jobId ? null : jobId;
    if (bg.openJob) await loadHistory(jobId);
    draw();
  }

  function renderCron() {
    const c = card('Scheduled jobs (cron)');
    const d = subEntry('cron').detail || {};
    const failed = new Set(d.failed || []);
    const overdue = new Set(d.overdue || []);
    if (d.scheduler_enabled === false) {
      c.appendChild(el('div', { class: 'err', text: 'Scheduler is OFF (MSB_CRON_ENABLED=0): no job will fire.' }));
    }
    if (!bg.cronJobs.length) {
      c.appendChild(el('div', { class: 'empty', text: 'No jobs.' }));
      return c;
    }
    const list = el('ul', { class: 'list' });
    for (const j of bg.cronJobs) {
      const li = el('li', {});
      if (!j.enabled) li.appendChild(el('span', { class: 'badge kind', text: 'OFF' }));
      else li.appendChild(stateBadge(failed.has(j.job_id) ? 'fail' : overdue.has(j.job_id) ? 'warn' : 'ok'));
      li.appendChild(el('strong', { text: ` ${j.job_id} ` }));
      li.appendChild(el('span', { class: 'detail', text: `${j.schedule} | next ${j.next_run || '?'}` }));
      const open = bg.openJob === j.job_id;
      li.appendChild(
        el('button', { class: 'btn', style: 'margin-left:8px', text: open ? 'Hide history' : 'History', onclick: () => toggleJob(j.job_id) })
      );
      if (open) {
        const runs = bg.cronHistory[j.job_id] || [];
        const hl = el('ul', { class: 'list' });
        for (const r of runs) {
          const ms = r.duration_ms == null ? '' : `${Math.round(r.duration_ms)}ms`;
          hl.appendChild(el('li', { class: 'mono', text: `${r.started_at}  ${r.status}  ${r.trigger}  ${ms}  ${r.error || ''}` }));
        }
        if (!runs.length) hl.appendChild(el('li', { class: 'empty', text: 'No runs recorded.' }));
        li.appendChild(hl);
      }
      list.appendChild(li);
    }
    c.appendChild(list);
    return c;
  }

  function renderGovernance() {
    const c = card('Governance (read-only)');
    const g = bg.governance;
    if (!g) {
      c.appendChild(el('div', { class: 'empty', text: 'No governance data yet.' }));
      return c;
    }
    const ks = g.killswitch || {};
    c.appendChild(el('div', {}, stateBadge(ks.armed ? 'fail' : 'ok'), ` Kill switch ${ks.armed ? 'ARMED' : 'off'}${ks.reason ? ` - ${ks.reason}` : ''}`));
    const scopes = (ks.scopes || []).filter((s) => !s.error);
    if (scopes.length) {
      c.appendChild(el('div', { class: 'detail', text: `armed scopes: ${scopes.map((s) => `${s.scope_type}:${s.scope_id}`).join(', ')}` }));
    }
    c.appendChild(el('h2', { text: 'Budgets', style: 'margin-top:12px' }));
    const bl = el('ul', { class: 'list' });
    for (const [cat, b] of Object.entries(g.budgets || {})) {
      const pct = b.limit > 0 ? Math.round((100 * b.spent) / b.limit) : null;
      bl.appendChild(
        el('li', {}, stateBadge(pct !== null && pct > 80 ? 'warn' : 'ok'), ` ${cat}: ${b.spent} / ${b.limit > 0 ? b.limit : 'unlimited'}${pct === null ? '' : ` (${pct}%)`}`)
      );
    }
    c.appendChild(bl);
    // GET /governance/approvals returns the whole queue, decided rows included,
    // so filter: this view is "pending approvals" (design doc), and the Overview
    // tile reports the same count from /governance/status.
    const pending = bg.approvals.filter((a) => String(a.status || '').toUpperCase() === 'PENDING');
    c.appendChild(el('h2', { text: `Pending approvals (${pending.length})`, style: 'margin-top:12px' }));
    const al = el('ul', { class: 'list' });
    for (const a of pending) al.appendChild(el('li', { text: `${a.kind || '?'}  ${a.id}  ${a.title || a.summary || a.reason || ''}` }));
    if (!pending.length) al.appendChild(el('li', { class: 'empty', text: 'None.' }));
    c.appendChild(al);
    const decided = bg.approvals.length - pending.length;
    c.appendChild(
      el('div', {
        class: 'detail',
        text:
          `Approve or reject from the main approvals panel; this view is watch-only.` +
          (decided ? ` ${decided} decided approval(s) in the queue are not listed here.` : ''),
      })
    );
    return c;
  }

  function renderAutomation() {
    const c = card('Automation & wake');
    for (const name of ['automation', 'wake']) {
      const e = subEntry(name);
      c.appendChild(el('div', { style: 'margin:8px 0' }, stateBadge(e.state), ` ${name}: ${e.error || summarize(name, e.detail || {})}`));
      c.appendChild(el('div', { class: 'mono', text: JSON.stringify(e.detail || {}, null, 2) }));
    }
    return c;
  }

  function renderPlei() {
    const c = card('PLEI calibration');
    const e = subEntry('plei');
    const d = e.detail || {};
    c.appendChild(el('div', {}, stateBadge(e.state), ` ${e.error || summarize('plei', d)}`));
    if (d.last_forecast_at) c.appendChild(el('div', { class: 'detail', text: `last prediction ${d.last_forecast_at}` }));
    if (d.chain_ok === false) c.appendChild(el('div', { class: 'err', text: `chain: ${d.chain_message}` }));
    c.appendChild(el('h2', { text: 'Calibration report', style: 'margin-top:12px' }));
    c.appendChild(el('div', { class: 'mono', text: bg.calibrate ? JSON.stringify(bg.calibrate, null, 2).slice(0, 4000) : 'loading...' }));
    return c;
  }

  window.MsbBackground = Object.freeze({ mount, unmount });
})();
