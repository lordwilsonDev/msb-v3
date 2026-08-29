'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const { MsbBridge } = require('../src/main/bridge');

/** Spin a throwaway HTTP server that records requests and replies per-route. */
function fakeMsb(routes) {
  const seen = [];
  const server = http.createServer((req, res) => {
    const chunks = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => {
      const body = Buffer.concat(chunks).toString('utf8');
      seen.push({ method: req.method, url: req.url, headers: req.headers, body });
      const key = `${req.method} ${req.url.split('?')[0]}`;
      const handler = routes[key] || routes[`${req.method} *`];
      if (!handler) {
        res.writeHead(404, { 'content-type': 'application/json' });
        return res.end(JSON.stringify({ detail: 'no route' }));
      }
      const { status = 200, json = {}, raw } = handler(seen[seen.length - 1]);
      res.writeHead(status, { 'content-type': 'application/json' });
      res.end(raw !== undefined ? raw : JSON.stringify(json));
    });
  });
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address();
      resolve({ server, port, seen, close: () => new Promise((r) => server.close(r)) });
    });
  });
}

test('identity() hits /status and flags the expected runtime', async () => {
  const m = await fakeMsb({
    'GET /status': () => ({ json: { service: 'msb-v3', version: '0.4.0', ready: true, model: 'qwen3:8b' } }),
  });
  const b = new MsbBridge('127.0.0.1', m.port, {});
  const r = await b.identity();
  await m.close();
  assert.equal(r.ok, true);
  assert.equal(r.data.expected, true);
  assert.equal(r.data.version, '0.4.0');
});

test('identity() marks a foreign service as not expected', async () => {
  const m = await fakeMsb({ 'GET /status': () => ({ json: { service: 'something-else' } }) });
  const b = new MsbBridge('127.0.0.1', m.port, {});
  const r = await b.identity();
  await m.close();
  assert.equal(r.ok, true);
  assert.equal(r.data.expected, false);
});

test('cockpit() hits /cockpit/api (JSON), not /cockpit (HTML)', async () => {
  const m = await fakeMsb({ 'GET /cockpit/api': () => ({ json: { panels: [] } }) });
  const b = new MsbBridge('127.0.0.1', m.port, {});
  const r = await b.cockpit();
  await m.close();
  assert.equal(r.ok, true);
  assert.equal(m.seen[0].url, '/cockpit/api');
});

test('approvals() normalises to { items: [] }', async () => {
  const m = await fakeMsb({ 'GET /governance/approvals': () => ({ json: { items: [{ id: 'a1', kind: 'build' }] } }) });
  const b = new MsbBridge('127.0.0.1', m.port, {});
  const r = await b.approvals();
  await m.close();
  assert.deepEqual(r.data.items, [{ id: 'a1', kind: 'build' }]);
});

test('approve() POSTs the path-param endpoint with a bearer token', async () => {
  const m = await fakeMsb({ 'POST /governance/approvals/a1/approve': () => ({ json: { id: 'a1', status: 'approved' } }) });
  const b = new MsbBridge('127.0.0.1', m.port, { operatorToken: 'SECRET-OP' });
  const r = await b.approve('a1', 'approve', 'looks fine');
  await m.close();
  assert.equal(r.ok, true);
  const req = m.seen[0];
  assert.equal(req.method, 'POST');
  assert.equal(req.url, '/governance/approvals/a1/approve');
  assert.equal(req.headers.authorization, 'Bearer SECRET-OP');
  assert.equal(JSON.parse(req.body).reason, 'looks fine');
});

test('approve() fails closed (503) when no operator token is configured - no request sent', async () => {
  const m = await fakeMsb({ 'POST *': () => ({ json: {} }) });
  const b = new MsbBridge('127.0.0.1', m.port, {}); // no token
  const r = await b.approve('a1', 'reject');
  await m.close();
  assert.equal(r.ok, false);
  assert.equal(r.status, 503);
  assert.equal(r.error, 'OPERATOR_TOKEN_NOT_CONFIGURED');
  assert.equal(m.seen.length, 0);
});

test('approve() rejects an action outside approve/reject before any network call', async () => {
  const m = await fakeMsb({ 'POST *': () => ({ json: {} }) });
  const b = new MsbBridge('127.0.0.1', m.port, { operatorToken: 'x' });
  const r = await b.approve('a1', 'cancel');
  await m.close();
  assert.equal(r.ok, false);
  assert.match(r.error, /INVALID_ACTION/);
  assert.equal(m.seen.length, 0);
});

test('memory() sends the x-mcp-secret header and encodes the session', async () => {
  const m = await fakeMsb({ 'GET /memory/sess-1': () => ({ json: { session: 'sess-1', messages: [] } }) });
  const b = new MsbBridge('127.0.0.1', m.port, { mcpSecret: 'MCP-123' });
  await b.memory('sess-1', 10);
  await m.close();
  assert.equal(m.seen[0].headers['x-mcp-secret'], 'MCP-123');
  assert.match(m.seen[0].url, /^\/memory\/sess-1\?limit=10$/);
});

test('search() posts tenant_id + query and optional X-API-Key', async () => {
  const m = await fakeMsb({ 'POST /rag/search': () => ({ json: { results: [] } }) });
  const b = new MsbBridge('127.0.0.1', m.port, { ragApiKey: 'RAG-9', ragTenant: 'wilson-vault' });
  await b.search('what is the gate', 3);
  await m.close();
  const req = m.seen[0];
  assert.equal(req.headers['x-api-key'], 'RAG-9');
  const sent = JSON.parse(req.body);
  assert.equal(sent.tenant_id, 'wilson-vault');
  assert.equal(sent.query, 'what is the gate');
  assert.equal(sent.limit, 3);
});

test('non-JSON response fails closed without throwing', async () => {
  const m = await fakeMsb({ 'GET /health': () => ({ raw: '<html>nope</html>' }) });
  const b = new MsbBridge('127.0.0.1', m.port, {});
  const r = await b.health();
  await m.close();
  assert.equal(r.ok, false);
  assert.match(r.error, /NON_JSON_RESPONSE/);
});

test('4xx maps to a stable error token', async () => {
  const m = await fakeMsb({ 'GET /memory/default': () => ({ status: 401, json: { detail: 'unauthorized' } }) });
  const b = new MsbBridge('127.0.0.1', m.port, {});
  const r = await b.memory('default', 10);
  await m.close();
  assert.equal(r.ok, false);
  assert.equal(r.status, 401);
  assert.match(r.error, /NOT_AUTHORISED/);
});

test('connection refused fails closed as MSB_UNREACHABLE', async () => {
  // Nothing listening on this port.
  const b = new MsbBridge('127.0.0.1', 1, {});
  const r = await b.health();
  assert.equal(r.ok, false);
  assert.match(r.error, /MSB_UNREACHABLE|REQUEST_INIT_FAILED/);
});

test('hasOperatorToken reflects configuration', () => {
  assert.equal(new MsbBridge('127.0.0.1', 8766, {}).hasOperatorToken(), false);
  assert.equal(new MsbBridge('127.0.0.1', 8766, { operatorToken: 'x' }).hasOperatorToken(), true);
});
