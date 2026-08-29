/**
 * MSB v3 Desktop — Renderer
 *
 * Minimal vanilla JS dashboard. No build step required.
 * Uses the typed API exposed by the preload script (window.msb).
 *
 * Sections:
 *   1. Connection status (attach → health → identity)
 *   2. Cockpit dashboard (from /cockpit)
 *   3. Approvals queue (from /governance/approvals)
 *   4. Kill switch status (from /governance/killswitch)
 *   5. Memory view (combined: session memory + vault search)
 */

'use strict';

// --- State ---

let state = {
  connected: false,
  health: null,
  identity: null,
  cockpit: null,
  approvals: [],
  killswitch: [],
  memory: [],
  searchResults: [],
  activeTab: 'cockpit',
  memorySession: 'default',
  searchQuery: '',
};

// --- API calls via preload bridge ---

async function attach() {
  const result = await window.msb.attach({});
  state.connected = result.ok;
  state.health = result.health || null;
  state.identity = result.identity || null;
  render();
  if (result.ok) {
    loadAll();
  }
}

async function loadAll() {
  const [cockpit, approvals, killswitch] = await Promise.all([
    window.msb.cockpit(),
    window.msb.approvals(),
    window.msb.killswitch(),
  ]);
  state.cockpit = cockpit;
  state.approvals = approvals.approvals || [];
  state.killswitch = killswitch.switches || [];
  render();
}

async function loadMemory() {
  const result = await window.msb.memory(state.memorySession, 20);
  state.memory = result.messages || [];
  render();
}

async function loadSearch() {
  if (!state.searchQuery.trim()) return;
  const result = await window.msb.search(state.searchQuery, 10);
  state.searchResults = result.results || [];
  render();
}

async function approveItem(id, action) {
  await window.msb.approve(id, action);
  await loadAll();
}

// --- Rendering ---

function render() {
  const root = document.getElementById('root');
  root.innerHTML = `
    <div class="header">
      <h1>MSB v3 — Sovereign Operations</h1>
      <span class="status ${state.connected ? 'connected' : 'disconnected'}">
        ${state.connected ? 'Connected' : 'Disconnected'}
      </span>
    </div>
    ${!state.connected ? renderConnect() : renderDashboard()}
  `;
}

function renderConnect() {
  return `
    <div class="card" style="max-width: 400px;">
      <h2>Connect to MSB v3</h2>
      <p style="font-size: 13px; color: #888; margin-bottom: 16px;">
        Connect to the msb-v3 server at 127.0.0.1:8766
      </p>
      <button class="btn" onclick="attach()">Connect</button>
    </div>
  `;
}

function renderDashboard() {
  return `
    <div class="grid">
      ${renderStatusCard()}
      ${renderIdentityCard()}
      ${renderApprovalsCard()}
      ${renderKillswitchCard()}
    </div>
    <div style="margin-top: 16px;">
      ${renderTabs()}
      ${state.activeTab === 'memory' ? renderMemoryView() : ''}
      ${state.activeTab === 'search' ? renderSearchView() : ''}
    </div>
  `;
}

function renderStatusCard() {
  const h = state.health || {};
  return `
    <div class="card">
      <h2>Server Health</h2>
      <div class="value" style="color: ${h.ok ? '#4ade80' : '#f87171'}">
        ${h.ok ? 'Healthy' : 'Unhealthy'}
      </div>
      <div class="detail">Version: ${h.version || '?'}</div>
      <div class="detail">Service: ${h.service || '?'}</div>
    </div>
  `;
}

function renderIdentityCard() {
  const id = state.identity || {};
  const config = id.config || {};
  return `
    <div class="card">
      <h2>System Identity</h2>
      <div class="value">MSB v3</div>
      <div class="detail">Version: ${config.version || '?'}</div>
      <div class="detail">Routes: ${config.route_count || '?'} mounted</div>
    </div>
  `;
}

function renderApprovalsCard() {
  const count = state.approvals.length;
  return `
    <div class="card">
      <h2>Pending Approvals</h2>
      <div class="value" style="color: ${count > 0 ? '#fbbf24' : '#4ade80'}">
        ${count}
      </div>
      <div class="detail">${count === 0 ? 'All clear' : 'Awaiting operator decision'}</div>
    </div>
  `;
}

function renderKillswitchCard() {
  const armed = state.killswitch.filter(s => s.armed).length;
  return `
    <div class="card">
      <h2>Kill Switches</h2>
      <div class="value" style="color: ${armed > 0 ? '#f87171' : '#4ade80'}">
        ${armed}/${state.killswitch.length}
      </div>
      <div class="detail">${armed === 0 ? 'All disarmed' : `${armed} armed`}</div>
    </div>
  `;
}

function renderTabs() {
  const tabs = [
    { id: 'memory', label: 'Memory' },
    { id: 'search', label: 'Vault Search' },
  ];
  return `
    <div style="display: flex; gap: 8px; margin-bottom: 16px;">
      ${tabs.map(t => `
        <button class="btn" style="background: ${state.activeTab === t.id ? '#2563eb' : '#1a1a1a'}; color: ${state.activeTab === t.id ? 'white' : '#888'};"
          onclick="switchTab('${t.id}')">
          ${t.label}
        </button>
      `).join('')}
    </div>
  `;
}

function renderMemoryView() {
  return `
    <div class="card">
      <h2>Session Memory</h2>
      <div style="margin-bottom: 12px;">
        <input type="text" value="${state.memorySession}" placeholder="session"
          style="background: #1a1a1a; border: 1px solid #2a2a2a; color: #e0e0e0; padding: 6px 12px; border-radius: 4px; margin-right: 8px;"
          onchange="state.memorySession = this.value; loadMemory();" />
        <button class="btn" onclick="loadMemory()">Load</button>
      </div>
      ${state.memory.length === 0 ? '<div class="empty">No messages in this session</div>' : `
        <ul class="list">
          ${state.memory.map(m => `
            <li>
              <span class="badge evidence">MSB-V3 EVIDENCE</span>
              <strong>${m.role || 'system'}:</strong> ${(m.content || '').substring(0, 200)}
            </li>
          `).join('')}
        </ul>
      `}
    </div>
  `;
}

function renderSearchView() {
  return `
    <div class="card">
      <h2>Vault Search</h2>
      <div style="margin-bottom: 12px;">
        <input type="text" value="${state.searchQuery}" placeholder="Search the vault..."
          style="background: #1a1a1a; border: 1px solid #2a2a2a; color: #e0e0e0; padding: 6px 12px; border-radius: 4px; width: 300px; margin-right: 8px;"
          onchange="state.searchQuery = this.value;" />
        <button class="btn" onclick="loadSearch()">Search</button>
      </div>
      ${state.searchResults.length === 0 ? '<div class="empty">No results</div>' : `
        <ul class="list">
          ${state.searchResults.map(r => `
            <li>
              <span class="badge vault">VAULT KNOWLEDGE</span>
              <strong>${r.title || r.source || 'result'}</strong>
              <div style="font-size: 12px; color: #666; margin-top: 4px;">
                ${(r.text || r.content || '').substring(0, 300)}
              </div>
            </li>
          `).join('')}
        </ul>
      `}
    </div>
  `;
}

// --- Tab switching ---

function switchTab(tab) {
  state.activeTab = tab;
  render();
}

// --- Init ---

render();
// Auto-attach on load
attach();
