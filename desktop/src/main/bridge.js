/**
 * MSB v3 Bridge - typed adapter to the msb-v3 runtime at 127.0.0.1:8766.
 *
 * Contract:
 *   - ATTACH ONLY. The bridge makes HTTP requests to an already-running,
 *     launchd-supervised server. It never spawns, kills, or restarts it.
 *   - FAIL CLOSED. Every public method resolves to a typed result and never
 *     rejects: { ok: true, data } | { ok: false, error, status? }.
 *   - Secrets (operator token, MCP bridge secret, RAG key) live in the main
 *     process only and are attached to requests here. They are never returned
 *     to the caller and never cross the preload boundary.
 *
 * Endpoint map verified live against msb-v3 v0.3.1 (/status), 2026-08-28.
 */

'use strict';

const http = require('http');

const DEFAULT_TIMEOUT_MS = 10000;
const EXPECTED_SERVICE = 'msb-v3';

/** @typedef {{ok: true, data: any} | {ok: false, error: string, status?: number}} BridgeResult */

class MsbBridge {
  /**
   * @param {string} host
   * @param {string|number} port
   * @param {{operatorToken?: string, mcpSecret?: string, ragApiKey?: string, ragTenant?: string}} [secrets]
   */
  constructor(host, port, secrets = {}) {
    this.host = host;
    this.port = String(port);
    this.baseUrl = `http://${host}:${this.port}`;
    this._operatorToken = secrets.operatorToken || '';
    this._mcpSecret = secrets.mcpSecret || '';
    this._ragApiKey = secrets.ragApiKey || '';
    this._ragTenant = secrets.ragTenant || 'wilson-vault';
  }

  /** True when an operator token is configured; gates the write surface in the UI. */
  hasOperatorToken() {
    return Boolean(this._operatorToken);
  }

  // --- transport ---------------------------------------------------------

  /**
   * One HTTP request. Resolves to a BridgeResult; never rejects.
   * @param {'GET'|'POST'} method
   * @param {string} path
   * @param {{body?: object, headers?: object, operator?: boolean}} [opts]
   * @returns {Promise<BridgeResult>}
   */
  _request(method, path, opts = {}) {
    return new Promise((resolve) => {
      let settled = false;
      const done = (r) => {
        if (settled) return;
        settled = true;
        resolve(r);
      };

      const headers = { Accept: 'application/json', ...(opts.headers || {}) };
      let payload = null;
      if (opts.body !== undefined) {
        payload = JSON.stringify(opts.body);
        headers['Content-Type'] = 'application/json';
        headers['Content-Length'] = Buffer.byteLength(payload);
      }
      if (opts.operator) {
        if (!this._operatorToken) {
          return done({ ok: false, error: 'OPERATOR_TOKEN_NOT_CONFIGURED', status: 503 });
        }
        headers.Authorization = `Bearer ${this._operatorToken}`;
      }

      let req;
      try {
        req = http.request(this.baseUrl + path, { method, headers, timeout: DEFAULT_TIMEOUT_MS }, (res) => {
          const chunks = [];
          res.on('data', (c) => chunks.push(c));
          res.on('end', () => {
            const raw = Buffer.concat(chunks).toString('utf8');
            const status = res.statusCode || 0;
            let data;
            try {
              data = raw ? JSON.parse(raw) : {};
            } catch {
              return done({ ok: false, error: `NON_JSON_RESPONSE (${status})`, status });
            }
            if (status < 200 || status >= 300) {
              const detail = (data && (data.detail || data.error)) || `HTTP ${status}`;
              return done({ ok: false, error: mapStatus(status, detail), status });
            }
            done({ ok: true, data });
          });
        });
      } catch (err) {
        return done({ ok: false, error: `REQUEST_INIT_FAILED: ${String(err && err.message || err)}` });
      }

      req.on('error', (err) => done({ ok: false, error: `MSB_UNREACHABLE: ${err.message}` }));
      req.on('timeout', () => {
        req.destroy();
        done({ ok: false, error: 'TIMEOUT' });
      });
      if (payload !== null) req.write(payload);
      req.end();
    });
  }

  _get(path, opts) {
    return this._request('GET', path, opts);
  }

  _post(path, opts) {
    return this._request('POST', path, opts);
  }

  // --- public API ------------------------------------------------------
  // Read endpoints are open. Write endpoints (approve/reject, killswitch)
  // pass operator:true and require MSB_OPERATOR_TOKEN.

  /** GET /health - liveness. */
  health() {
    return this._get('/health');
  }

  /**
   * GET /status - runtime identity. Confirms we are attached to the
   * EXPECTED runtime, not merely to something answering on :8766.
   */
  async identity() {
    const r = await this._get('/status');
    if (!r.ok) return r;
    const d = r.data || {};
    const expected = d.service === EXPECTED_SERVICE;
    return {
      ok: true,
      data: {
        service: d.service,
        version: d.version,
        ready: Boolean(d.ready),
        model: d.model,
        host: d.host,
        port: d.port,
        expected,
      },
    };
  }

  /** GET /cockpit/api - aggregated dashboard state (JSON; /cockpit is HTML). */
  cockpit() {
    return this._get('/cockpit/api');
  }

  /** GET /governance/status - killswitch + budgets + governor + approvals summary. */
  governanceStatus() {
    return this._get('/governance/status');
  }

  /** GET /governance/approvals - full pending queue. Normalised to { items: [...] }. */
  async approvals() {
    const r = await this._get('/governance/approvals');
    if (!r.ok) return r;
    const items = Array.isArray(r.data?.items) ? r.data.items : [];
    return { ok: true, data: { items } };
  }

  /**
   * POST /governance/approvals/{id}/{approve|reject} - operator only.
   * @param {string} id
   * @param {'approve'|'reject'} action
   * @param {string} [reason]
   */
  approve(id, action, reason) {
    if (action !== 'approve' && action !== 'reject') {
      return Promise.resolve({ ok: false, error: `INVALID_ACTION: ${action}` });
    }
    const seg = encodeURIComponent(id);
    return this._post(`/governance/approvals/${seg}/${action}`, {
      operator: true,
      body: { operator: 'desktop-cockpit', reason: reason || `${action} via desktop cockpit` },
    });
  }

  /**
   * POST /governance/killswitch/{arm|disarm} - operator only.
   * @param {'arm'|'disarm'} op
   * @param {string} [reason]
   */
  killswitchSet(op, reason) {
    if (op !== 'arm' && op !== 'disarm') {
      return Promise.resolve({ ok: false, error: `INVALID_OP: ${op}` });
    }
    return this._post(`/governance/killswitch/${op}`, {
      operator: true,
      body: { operator: 'desktop-cockpit', reason: reason || `${op} via desktop cockpit` },
    });
  }

  /**
   * GET /memory/{session} - conversation/evidence memory. Gated by
   * MCP_BRIDGE_SECRET (x-mcp-secret header).
   * @param {string} session
   * @param {number} limit
   */
  memory(session, limit) {
    const seg = encodeURIComponent(session);
    const headers = this._mcpSecret ? { 'x-mcp-secret': this._mcpSecret } : {};
    return this._get(`/memory/${seg}?limit=${encodeURIComponent(limit)}`, { headers });
  }

  /**
   * POST /rag/search - semantic search over the vault index. Optional
   * X-API-Key (MSB_RAG_API_KEY) when the server enforces it.
   * @param {string} query
   * @param {number} limit
   */
  search(query, limit) {
    const headers = this._ragApiKey ? { 'X-API-Key': this._ragApiKey } : {};
    return this._post('/rag/search', {
      headers,
      body: { tenant_id: this._ragTenant, query, limit },
    });
  }
}

/** Map an HTTP status to a stable, renderer-safe error token. */
function mapStatus(status, detail) {
  switch (status) {
    case 401:
    case 403:
      return `NOT_AUTHORISED: ${detail}`;
    case 404:
      return `NOT_FOUND: ${detail}`;
    case 409:
      return `CONFLICT: ${detail}`;
    case 422:
      return `INVALID_REQUEST: ${detail}`;
    case 503:
      return `MSB_UNAVAILABLE: ${detail}`;
    default:
      return `HTTP_${status}: ${detail}`;
  }
}

module.exports = { MsbBridge, EXPECTED_SERVICE };
