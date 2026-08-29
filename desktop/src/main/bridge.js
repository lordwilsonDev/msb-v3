/**
 * MSB v3 Bridge — typed adapter to the msb-v3 server at 127.0.0.1:8766.
 *
 * Every method:
 *   1. Makes a single HTTP request to the msb-v3 API
 *   2. Parses the JSON response
 *   3. Returns a typed result (never throws — errors are returned as {ok: false, error})
 *   4. Is fail-closed (bridge error = error result, not exception)
 *
 * The bridge is stateless — it holds only the base URL. Authentication
 * (operator token) is handled by the main process if needed.
 */

'use strict';

const http = require('http');

class MsbBridge {
  constructor(host, port) {
    this.baseUrl = `http://${host}:${port}`;
  }

  /**
   * Make a GET request to the msb-v3 API.
   * Returns parsed JSON or throws on network/parse error.
   */
  async _get(path) {
    return new Promise((resolve, reject) => {
      const url = `${this.baseUrl}${path}`;
      http.get(url, { timeout: 10000 }, (res) => {
        let data = '';
        res.on('data', (chunk) => { data += chunk; });
        res.on('end', () => {
          try {
            resolve(JSON.parse(data));
          } catch (err) {
            reject(new Error(`JSON parse error: ${err.message}`));
          }
        });
      }).on('error', (err) => {
        reject(new Error(`HTTP error: ${err.message}`));
      }).on('timeout', function () {
        this.destroy();
        reject(new Error('Request timeout'));
      });
    });
  }

  /**
   * Make a POST request to the msb-v3 API.
   */
  async _post(path, body) {
    return new Promise((resolve, reject) => {
      const url = `${this.baseUrl}${path}`;
      const payload = JSON.stringify(body || {});
      const req = http.request(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Content-Length': Buffer.byteLength(payload),
        },
        timeout: 10000,
      }, (res) => {
        let data = '';
        res.on('data', (chunk) => { data += chunk; });
        res.on('end', () => {
          try {
            resolve(JSON.parse(data));
          } catch (err) {
            reject(new Error(`JSON parse error: ${err.message}`));
          }
        });
      });
      req.on('error', (err) => {
        reject(new Error(`HTTP error: ${err.message}`));
      });
      req.on('timeout', () => {
        req.destroy();
        reject(new Error('Request timeout'));
      });
      req.write(payload);
      req.end();
    });
  }

  // --- Public API ---

  async health() {
    return this._get('/health');
  }

  async identity() {
    return this._get('/system/health');
  }

  async cockpit() {
    return this._get('/cockpit');
  }

  async approvals() {
    return this._get('/governance/approvals');
  }

  async approve(id, action) {
    return this._post('/governance/approve', { id, action });
  }

  async killswitch() {
    return this._get('/governance/killswitch');
  }

  async memory(session, limit) {
    const q = limit ? `?limit=${limit}` : '';
    return this._get(`/memory/${session || 'default'}${q}`);
  }

  async search(query, limit) {
    return this._post('/rag/search', { query, limit: limit || 10 });
  }
}

module.exports = { MsbBridge };
