'use strict';

/**
 * Governance-boundary tests: prove the desktop is desktop -> MSB only.
 * No button, bridge method, or IPC handler may reach a tool, a provider, a
 * shell, or an evidence store directly. Everything consequential goes through
 * an msb-v3 /governance endpoint.
 */

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const SRC = path.join(__dirname, '..', 'src');
const read = (p) => fs.readFileSync(path.join(SRC, p), 'utf8');

const bridge = read('main/bridge.js');
const mainIndex = read('main/index.js');

/** Extract every HTTP path the bridge requests. */
function bridgePaths() {
  return [...bridge.matchAll(/_(get|post)\(\s*`?['"`]([^'"`]+)['"`]/g)].map((m) => ({
    method: m[1].toUpperCase(),
    path: m[2].split('?')[0],
  }));
}

test('bridge only calls a known, safe set of msb-v3 endpoints', () => {
  const allowed = new Set([
    'GET /health',
    'GET /status',
    'GET /cockpit/api',
    'GET /governance/status',
    'GET /governance/approvals',
    'GET /memory/', // prefix, session appended
    'POST /rag/search',
  ]);
  const allowedPrefixes = ['POST /governance/approvals/', 'POST /governance/killswitch/', 'GET /memory/'];

  for (const { method, path: p } of bridgePaths()) {
    const key = `${method} ${p}`;
    const okExact = allowed.has(key);
    const okPrefix = allowedPrefixes.some((pre) => key.startsWith(pre));
    assert.ok(okExact || okPrefix, `unexpected endpoint: ${key}`);
  }
});

test('every state-changing call is a /governance endpoint carrying the operator token', () => {
  // POSTs that mutate runtime state must set operator:true.
  const posts = [...bridge.matchAll(/_post\(\s*`?([^,]+?)`?,\s*\{([\s\S]*?)\}\s*\);/g)];
  let mutating = 0;
  for (const [, target, opts] of posts) {
    if (/governance\/(approvals|killswitch)/.test(target)) {
      mutating++;
      assert.match(opts, /operator:\s*true/, `missing operator gate for ${target.trim()}`);
    }
  }
  assert.ok(mutating >= 2, 'expected approve + killswitch mutating calls');
});

test('bridge has no tool / provider / factory / agent execution path', () => {
  assert.doesNotMatch(bridge, /\/agent\/|\/factory\/|\/tools?\/|\/providers?\/|\/ralph|\/flywheel\/turn|ollama|deepseek/i);
});

test('secrets never leave the main process', () => {
  // No IPC handler returns a token; identity() explicitly strips to safe fields.
  assert.doesNotMatch(mainIndex, /return[^;]*(operatorToken|mcpSecret|ragApiKey)/);
  assert.doesNotMatch(bridge, /data:\s*\{[^}]*_operatorToken/);
  // hasOperatorToken returns a boolean, not the value.
  assert.match(bridge, /hasOperatorToken\(\)\s*\{\s*return Boolean/);
});

test('killswitch is read via /governance/status, not a second local switch', () => {
  assert.doesNotMatch(mainIndex, /new KillSwitch|localKillSwitch|this\.killswitch\s*=/);
  assert.match(mainIndex, /killswitch.*governanceStatus/s);
});

test('attach verifies runtime identity before declaring READY', () => {
  assert.match(mainIndex, /identity\.data\.expected/);
  assert.match(mainIndex, /WRONG_RUNTIME/);
  assert.match(mainIndex, /state:\s*'BLOCKED'/);
});
