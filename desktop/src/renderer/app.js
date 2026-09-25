/**
 * MSB v3 Desktop - Renderer
 *
 * Vanilla JS, no build step. Talks only to window.msb (preload bridge).
 *
 * Security notes:
 *   - All server-derived text goes in via textContent / DOM nodes, never
 *     innerHTML. innerHTML is used only for static, code-defined markup.
 *   - No inline event handlers (CSP: script-src 'self'); everything is
 *     wired with addEventListener.
 *   - The renderer has no network access (connect-src 'none'); every call
 *     is an IPC round-trip the main process validates.
 */

'use strict';

// --- tiny DOM helper --------------------------------------------------

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === 'class') node.className = v;
    else if (k === 'text') node.textContent = v;
    else if (k === 'html') node.innerHTML = v; // static strings only
    else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null) node.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c == null) continue;
    node.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
  }
  return node;
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

// --- state ---------------------------------------------------------

const state = {
  runtimeState: 'NOT_ATTACHED',
  attachDetail: '',
  operator: false,
  health: null,
  identity: null,
  governance: null,
  approvals: [],
  memory: [],
  searchResults: [],
  activeTab: 'memory',
  memorySession: 'default',
  searchQuery: '',
  chatDraft: '',
  chatSending: false,
  notice: '',
  tasks: {
    list: [],
    expandedId: null,
    // taskId -> { events: Array<{event:string, data:object}>, status: 'idle'|'streaming'|'reconnecting'|'error', errorDetail: string }
    streams: {},
  },
};

const MAX_TASK_EVENTS = 200;
let taskEventsListEl = null; // the <ul> for the currently-expanded task, for append-only updates

// --- data loads (all fail-closed) ---------------------------------

async function attach() {
  state.notice = '';
  const r = await window.msb.attach({});
  state.runtimeState = r.state || (r.ok ? 'READY' : 'OFFLINE');
  state.attachDetail = r.detail || r.error || '';
  state.operator = Boolean(r.operator);
  state.health = r.health || null;
  state.identity = r.identity || null;
  render();
  if (r.ok && state.runtimeState !== 'OFFLINE') loadAll();
}

async function loadAll() {
  const [gov, approvals] = await Promise.all([
    window.msb.governanceStatus(),
    window.msb.approvals(),
  ]);
  state.governance = gov.ok ? gov.data : null;
  state.approvals = approvals.ok && approvals.data ? approvals.data.items || [] : [];
  if (!gov.ok) state.notice = `governance: ${gov.error}`;
  render();
}

async function loadMemory() {
  const r = await window.msb.memory(state.memorySession, 25);
  state.memory = r.ok && r.data ? r.data.messages || [] : [];
  state.notice = r.ok ? '' : `memory: ${r.error}`;
  render();
}

async function sendChatMessage() {
  const q = state.chatDraft.trim();
  if (!q || state.chatSending) return;
  state.chatSending = true;
  state.chatDraft = '';
  render();
  const r = await window.msb.sendChat(q, state.memorySession);
  state.chatSending = false;
  state.notice = r.ok ? '' : `chat: ${r.error}`;
  await loadMemory();
}

async function loadSearch() {
  const q = state.searchQuery.trim();
  if (!q) return;
  const r = await window.msb.search(q, 10);
  const d = r.ok ? r.data || {} : {};
  state.searchResults = d.results || d.hits || d.matches || [];
  state.notice = r.ok ? '' : `search: ${r.error}`;
  render();
}

async function decide(id, action) {
  state.notice = `${action}...`;
  render();
  const r = await window.msb.approve(id, action);
  state.notice = r.ok ? `${action}d ${id}` : `${action} failed: ${r.error}`;
  await loadAll();
}

async function killswitch(op) {
  state.notice = `${op} kill switch...`;
  render();
  const r = await window.msb.killswitchSet(op);
  state.notice = r.ok ? `kill switch ${op}ed` : `${op} failed: ${r.error}`;
  await loadAll();
}

// --- data loads: tasks ---------------------------------------------

async function loadTasks() {
  const r = await window.msb.listTasks(25);
  state.tasks.list = r.ok && r.data ? r.data.tasks || [] : [];
  state.notice = r.ok ? state.notice : `tasks: ${r.error}`;
  renderTabBody();
}

function taskState(taskId) {
  if (!state.tasks.streams[taskId]) {
    state.tasks.streams[taskId] = { events: [], status: 'idle', errorDetail: '' };
  }
  return state.tasks.streams[taskId];
}

async function expandTask(taskId) {
  if (state.tasks.expandedId === taskId) {
    // collapse
    await window.msb.unsubscribeTask(taskId);
    state.tasks.expandedId = null;
    taskEventsListEl = null;
    renderTabBody();
    return;
  }
  if (state.tasks.expandedId) {
    await window.msb.unsubscribeTask(state.tasks.expandedId);
  }
  state.tasks.expandedId = taskId;
  const ts = taskState(taskId);
  ts.status = 'streaming';
  taskEventsListEl = null; // rebuilt on next renderTabBody()
  renderTabBody();
  await window.msb.subscribeTask(taskId);
}

/** Wired once at init via window.msb.onTaskEvent. */
function handleTaskEvent(evt) {
  const ts = taskState(evt.taskId);
  if (evt.event === 'observation' || evt.event === 'done') {
    ts.status = evt.event === 'done' ? 'idle' : 'streaming';
    ts.events.push(evt);
    if (ts.events.length > MAX_TASK_EVENTS) ts.events.shift();
  } else if (evt.event === 'reconnecting') {
    ts.status = 'reconnecting';
  } else if (evt.event === 'stream-error') {
    ts.status = 'error';
    ts.errorDetail = evt.data && evt.data.error ? evt.data.error : 'unknown error';
  }

  // Append-only fast path: if this is the currently-expanded task and its
  // event list DOM already exists, just append the new row instead of
  // re-rendering the whole Tasks panel (no flicker on a live-streaming task).
  if (evt.taskId === state.tasks.expandedId && taskEventsListEl && (evt.event === 'observation' || evt.event === 'done')) {
    taskEventsListEl.appendChild(taskEventRow(evt));
    while (taskEventsListEl.childElementCount > MAX_TASK_EVENTS) taskEventsListEl.removeChild(taskEventsListEl.firstChild);
    return;
  }
  // Anything else (status change, or an event for a task whose panel isn't
  // mounted) needs a full re-render of just the tab body.
  if (state.activeTab !== 'tasks') return;
  renderTabBody();
}

// --- rendering ---------------------------------------------------

let containers = null; // { header, grid, approvals, tabs, tabBody }, built once

function layout() {
  if (containers) return containers;
  const root = document.getElementById('root');
  clear(root);
  const header = el('div', { class: 'header' });
  const body = el('div', {});
  root.appendChild(header);
  root.appendChild(body);
  containers = { root, header, body, grid: null, approvals: null, tabs: null, tabBody: null };
  return containers;
}

function renderHeader() {
  const c = layout();
  clear(c.header);
  const badgeClass =
    state.runtimeState === 'READY' ? 'connected' : state.runtimeState === 'DEGRADED' ? 'degraded' : 'disconnected';
  c.header.appendChild(el('h1', { text: 'MSB v3 - Sovereign Operations' }));
  c.header.appendChild(el('span', { class: `status ${badgeClass}`, text: state.runtimeState }));
}

function render() {
  window.__runtimeState = state.runtimeState; // dev/smoke visibility
  const c = layout();
  renderHeader();
  clear(c.body);

  if (state.notice) c.body.appendChild(el('div', { class: 'notice', text: state.notice }));

  if (state.runtimeState === 'NOT_ATTACHED' || state.runtimeState === 'OFFLINE' || state.runtimeState === 'BLOCKED') {
    c.body.appendChild(renderConnect());
    c.grid = c.approvals = c.tabs = c.tabBody = null;
    window.MsbBackground.unmount(); // nothing to watch without a runtime
    return;
  }

  c.grid = el('div', { class: 'grid' });
  c.approvals = el('div', { style: 'margin-top:16px' });
  c.tabs = el('div', {});
  c.tabBody = el('div', {});
  c.body.appendChild(c.grid);
  c.body.appendChild(c.approvals);
  c.body.appendChild(c.tabs);
  c.body.appendChild(c.tabBody);

  renderStatCards();
  renderApprovals();
  renderTabsBar();
  renderTabBody();
}

function renderStatCards() {
  const c = layout();
  if (!c.grid) return;
  clear(c.grid);
  c.grid.appendChild(
    statCard('Server', state.health ? 'Healthy' : 'Unknown', state.health ? '#4ade80' : '#888', [
      `service: ${val(state.identity, 'service')}`,
      `version: ${val(state.identity, 'version')}`,
    ])
  );
  const verified = Boolean(state.identity && state.identity.expected === true);
  c.grid.appendChild(
    statCard('Runtime Identity', verified ? 'Verified' : 'Unverified', verified ? '#4ade80' : '#f87171', [
      `model: ${val(state.identity, 'model')}`,
      `ready: ${val(state.identity, 'ready')}`,
    ])
  );
  const pending = pendingApprovals().length;
  c.grid.appendChild(
    statCard('Pending Approvals', String(pending), pending ? '#fbbf24' : '#4ade80', [
      pending ? 'awaiting operator' : 'all clear',
      `${state.approvals.length} in history`,
    ])
  );
  c.grid.appendChild(renderKillswitchCard());
}

function renderApprovals() {
  const c = layout();
  if (!c.approvals) return;
  clear(c.approvals);
  c.approvals.appendChild(renderApprovalsPanel());
}

function renderTabsBar() {
  const c = layout();
  if (!c.tabs) return;
  clear(c.tabs);
  const bar = el('div', { style: 'display:flex; gap:8px; margin:16px 0' });
  for (const t of [
    { id: 'memory', label: 'Evidence Memory' },
    { id: 'search', label: 'Vault Knowledge' },
    { id: 'tasks', label: 'Live Tasks' },
    { id: 'background', label: 'Background' },
  ]) {
    bar.appendChild(
      el('button', {
        class: 'btn',
        style: `background:${state.activeTab === t.id ? '#2563eb' : '#1a1a1a'}`,
        text: t.label,
        onclick: () => {
          state.activeTab = t.id;
          renderTabsBar();
          renderTabBody();
          if (t.id === 'tasks' && !state.tasks.list.length) loadTasks();
        },
      })
    );
  }
  c.tabs.appendChild(bar);
}

function renderTabBody() {
  const c = layout();
  if (!c.tabBody) return;
  clear(c.tabBody);
  taskEventsListEl = null; // will be re-set by renderTasks() if the tasks tab renders an expanded task
  if (state.activeTab !== 'background') window.MsbBackground.unmount();
  if (state.activeTab === 'memory') c.tabBody.appendChild(renderMemory());
  if (state.activeTab === 'search') c.tabBody.appendChild(renderSearch());
  if (state.activeTab === 'tasks') c.tabBody.appendChild(renderTasks());
  if (state.activeTab === 'background') c.tabBody.appendChild(window.MsbBackground.mount());
}

function renderConnect() {
  const card = el('div', { class: 'card', style: 'max-width:460px' });
  card.appendChild(el('h2', { text: 'Attach to MSB v3' }));
  card.appendChild(
    el('p', {
      class: 'muted',
      text: 'Client only - launchd supervises the runtime. This attaches to 127.0.0.1:8766.',
    })
  );
  if (state.attachDetail) card.appendChild(el('p', { class: 'err', text: state.attachDetail }));
  card.appendChild(el('button', { class: 'btn', text: 'Attach', onclick: attach }));
  return card;
}

function statCard(title, value, color, details) {
  const card = el('div', { class: 'card' });
  card.appendChild(el('h2', { text: title }));
  card.appendChild(el('div', { class: 'value', style: `color:${color}`, text: value }));
  for (const d of details || []) card.appendChild(el('div', { class: 'detail', text: d }));
  return card;
}

function renderKillswitchCard() {
  const ks = state.governance && state.governance.killswitch ? state.governance.killswitch : {};
  const armed = ks.armed === true;
  const card = el('div', { class: 'card' });
  card.appendChild(el('h2', { text: 'Kill Switch' }));
  card.appendChild(
    el('div', { class: 'value', style: `color:${armed ? '#f87171' : '#4ade80'}`, text: armed ? 'ARMED' : 'disarmed' })
  );
  if (!state.operator) {
    card.appendChild(el('div', { class: 'detail', text: 'operator token not set - control disabled' }));
    return card;
  }
  const row = el('div', { style: 'margin-top:8px; display:flex; gap:8px' });
  row.appendChild(el('button', { class: 'btn danger', text: 'Arm', onclick: () => killswitch('arm') }));
  row.appendChild(el('button', { class: 'btn', text: 'Disarm', onclick: () => killswitch('disarm') }));
  card.appendChild(row);
  return card;
}

function pendingApprovals() {
  return state.approvals.filter((a) => String(a.status || '').toUpperCase() === 'PENDING');
}

function approvalRow(a, actionable) {
  const li = el('li', {});
  li.appendChild(el('span', { class: 'badge kind', text: String(a.kind || 'action') }));
  li.appendChild(el('strong', { text: ` ${a.title || a.id} ` }));
  li.appendChild(el('span', { class: 'detail', text: `(${a.status || '?'})` }));
  if (Array.isArray(a.evidence_refs) && a.evidence_refs.length) {
    const insp = el('details', { style: 'margin-top:4px' });
    insp.appendChild(el('summary', { class: 'detail', text: `inspect - ${a.evidence_refs.length} evidence ref(s)` }));
    for (const ref of a.evidence_refs) insp.appendChild(el('div', { class: 'mono', text: String(ref) }));
    li.appendChild(insp);
  }
  if (actionable && state.operator) {
    const row = el('div', { style: 'margin-top:6px; display:flex; gap:8px' });
    row.appendChild(el('button', { class: 'btn', text: 'Approve', onclick: () => decide(a.id, 'approve') }));
    row.appendChild(el('button', { class: 'btn danger', text: 'Reject', onclick: () => decide(a.id, 'reject') }));
    li.appendChild(row);
  }
  return li;
}

function renderApprovalsPanel() {
  const card = el('div', { class: 'card', style: 'margin-top:16px' });
  card.appendChild(el('h2', { text: 'Approval Queue' }));

  const pending = pendingApprovals();
  if (!pending.length) {
    card.appendChild(el('div', { class: 'empty', text: 'Nothing pending.' }));
  } else {
    if (!state.operator) {
      card.appendChild(el('div', { class: 'detail', text: 'operator token not set - approve/reject disabled' }));
    }
    const list = el('ul', { class: 'list' });
    for (const a of pending) list.appendChild(approvalRow(a, true));
    card.appendChild(list);
  }

  const decided = state.approvals.filter((a) => String(a.status || '').toUpperCase() !== 'PENDING');
  if (decided.length) {
    const hist = el('details', { style: 'margin-top:12px' });
    hist.appendChild(el('summary', { class: 'detail', text: `decided - ${decided.length}` }));
    const list = el('ul', { class: 'list' });
    for (const a of decided.slice(0, 25)) list.appendChild(approvalRow(a, false));
    hist.appendChild(list);
    card.appendChild(hist);
  }
  return card;
}

function renderMemory() {
  const card = el('div', { class: 'card' });
  card.appendChild(el('h2', { text: 'Session Memory' }));
  const controls = el('div', { style: 'margin-bottom:12px; display:flex; gap:8px' });
  const input = el('input', { type: 'text', value: state.memorySession, placeholder: 'session', class: 'in' });
  input.addEventListener('change', (e) => {
    state.memorySession = e.target.value;
  });
  controls.appendChild(input);
  controls.appendChild(el('button', { class: 'btn', text: 'Load', onclick: loadMemory }));
  card.appendChild(controls);

  const chatRow = el('div', { style: 'margin-bottom:12px; display:flex; gap:8px' });
  const chatInput = el('input', {
    type: 'text',
    value: state.chatDraft,
    placeholder: 'message the model...',
    class: 'in',
    style: 'width:320px',
  });
  chatInput.addEventListener('input', (e) => {
    state.chatDraft = e.target.value;
  });
  chatInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') sendChatMessage();
  });
  chatRow.appendChild(chatInput);
  chatRow.appendChild(el('button', { class: 'btn', text: state.chatSending ? 'Sending...' : 'Send', onclick: sendChatMessage }));
  card.appendChild(chatRow);

  if (!state.memory.length) {
    card.appendChild(el('div', { class: 'empty', text: 'No messages for this session.' }));
    return card;
  }
  const list = el('ul', { class: 'list' });
  for (const m of state.memory) {
    const li = el('li', {});
    li.appendChild(el('span', { class: 'badge evidence', text: 'MSB-V3 EVIDENCE' }));
    li.appendChild(el('strong', { text: ` ${m.role || 'system'}: ` }));
    li.appendChild(document.createTextNode(String(m.content || '').slice(0, 400)));
    list.appendChild(li);
  }
  card.appendChild(list);
  return card;
}

function renderSearch() {
  const card = el('div', { class: 'card' });
  card.appendChild(el('h2', { text: 'Vault Knowledge Search' }));
  const controls = el('div', { style: 'margin-bottom:12px; display:flex; gap:8px' });
  const input = el('input', { type: 'text', value: state.searchQuery, placeholder: 'search the vault...', class: 'in', style: 'width:320px' });
  input.addEventListener('input', (e) => {
    state.searchQuery = e.target.value;
  });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') loadSearch();
  });
  controls.appendChild(input);
  controls.appendChild(el('button', { class: 'btn', text: 'Search', onclick: loadSearch }));
  card.appendChild(controls);

  if (!state.searchResults.length) {
    card.appendChild(el('div', { class: 'empty', text: 'No results.' }));
    return card;
  }
  const list = el('ul', { class: 'list' });
  for (const r of state.searchResults) {
    const li = el('li', {});
    li.appendChild(el('span', { class: 'badge vault', text: 'VAULT KNOWLEDGE' }));
    li.appendChild(el('strong', { text: ` ${r.source || r.title || 'result'} ` }));
    if (typeof r.score === 'number') li.appendChild(el('span', { class: 'detail', text: `score ${r.score.toFixed(2)}` }));
    li.appendChild(el('div', { class: 'detail', text: String(r.text || r.content || '').slice(0, 400) }));
    list.appendChild(li);
  }
  card.appendChild(list);
  return card;
}

function taskEventRow(evt) {
  const li = el('li', {});
  li.appendChild(el('span', { class: 'badge kind', text: evt.event }));
  const d = evt.data || {};
  const summary = d.source || d.state || '';
  li.appendChild(el('strong', { text: ` ${summary || '(no source)'} ` }));
  if (d.observed_at) li.appendChild(el('span', { class: 'detail', text: String(d.observed_at) }));
  li.appendChild(el('div', { class: 'mono', text: JSON.stringify(d) }));
  return li;
}

function renderTasks() {
  const card = el('div', { class: 'card' });
  card.appendChild(el('h2', { text: 'Live Tasks' }));
  const controls = el('div', { style: 'margin-bottom:12px' });
  controls.appendChild(el('button', { class: 'btn', text: 'Refresh', onclick: loadTasks }));
  card.appendChild(controls);

  if (!state.tasks.list.length) {
    card.appendChild(el('div', { class: 'empty', text: 'No tasks yet.' }));
    return card;
  }

  const list = el('ul', { class: 'list' });
  for (const t of state.tasks.list) {
    const li = el('li', {});
    const expanded = state.tasks.expandedId === t.task_id;
    li.appendChild(el('span', { class: 'badge kind', text: t.state }));
    li.appendChild(el('strong', { text: ` ${t.task_id} ` }));
    li.appendChild(el('span', { class: 'detail', text: `updated ${t.updated_at || '?'}` }));
    li.appendChild(
      el('button', {
        class: 'btn',
        style: 'margin-left:8px',
        text: expanded ? 'Collapse' : 'Watch live',
        onclick: () => expandTask(t.task_id),
      })
    );

    if (expanded) {
      const ts = taskState(t.task_id);
      const box = el('div', { style: 'margin-top:8px' });
      if (ts.status === 'reconnecting') box.appendChild(el('div', { class: 'detail', text: 'reconnecting...' }));
      if (ts.status === 'error') box.appendChild(el('div', { class: 'err', text: `stream error: ${ts.errorDetail}` }));
      const events = el('ul', { class: 'list' });
      for (const evt of ts.events) events.appendChild(taskEventRow(evt));
      box.appendChild(events);
      li.appendChild(box);
      taskEventsListEl = events; // remember for append-only updates
    }
    list.appendChild(li);
  }
  card.appendChild(list);
  return card;
}

// --- utils -------------------------------------------------------

function val(obj, key) {
  if (!obj || obj[key] === undefined || obj[key] === null) return '?';
  return String(obj[key]);
}

// --- init ------------------------------------------------------

render();
window.msb.onTaskEvent(handleTaskEvent);
attach();
