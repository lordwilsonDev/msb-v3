'use strict';

/**
 * Security-boundary tests. These are source-level assertions: they fail if a
 * future edit weakens the renderer isolation, widens the preload surface, or
 * opens a shell/filesystem/network path in the main process.
 */

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const SRC = path.join(__dirname, '..', 'src');
const read = (p) => fs.readFileSync(path.join(SRC, p), 'utf8');

const mainIndex = read('main/index.js');
const preload = read('preload/index.js');
const bridge = read('main/bridge.js');
const rendererHtml = read('renderer/index.html');
const rendererApp = read('renderer/app.js');

test('BrowserWindow is hardened: no node, isolated, sandboxed', () => {
  assert.match(mainIndex, /nodeIntegration:\s*false/);
  assert.match(mainIndex, /contextIsolation:\s*true/);
  assert.match(mainIndex, /sandbox:\s*true/);
  assert.match(mainIndex, /webviewTag:\s*false/);
  assert.match(mainIndex, /app\.enableSandbox\(\)/);
});

test('navigation and new windows are denied', () => {
  assert.match(mainIndex, /setWindowOpenHandler/);
  assert.match(mainIndex, /return\s*\{\s*action:\s*'deny'\s*\}/);
  assert.match(mainIndex, /will-navigate/);
  assert.match(mainIndex, /will-attach-webview/);
  assert.match(mainIndex, /web-contents-created/);
});

test('permission requests are denied and a CSP header is set', () => {
  assert.match(mainIndex, /setPermissionRequestHandler\(\s*\(.*\)\s*=>\s*cb\(false\)\s*\)/s);
  assert.match(mainIndex, /Content-Security-Policy/);
  assert.match(rendererHtml, /http-equiv="Content-Security-Policy"/);
  assert.match(rendererHtml, /default-src 'none'/);
  assert.match(rendererHtml, /connect-src 'none'/);
});

test('main process never spawns a child process or a shell', () => {
  for (const [name, src] of [['index.js', mainIndex], ['bridge.js', bridge]]) {
    assert.doesNotMatch(src, /child_process|execSync|execFile|spawnSync|\bspawn\(|\bexec\(/, name);
  }
});

test('main process does not read or write the filesystem beyond loading the renderer', () => {
  // path.join is fine; fs.* is not.
  assert.doesNotMatch(mainIndex, /require\(['"]fs['"]\)|require\(['"]node:fs['"]\)|fs\.(readFile|writeFile|readdir|unlink)/);
});

test('preload exposes exactly the allow-listed method names', () => {
  const exposed = [...preload.matchAll(/^\s*([a-zA-Z]+):\s*\(/gm)].map((m) => m[1]);
  const expected = [
    'attach', 'health', 'identity', 'cockpit', 'governanceStatus',
    'approvals', 'approve', 'killswitch', 'killswitchSet', 'memory', 'search',
  ].sort();
  assert.deepEqual([...new Set(exposed)].sort(), expected);
});

test('preload has no generic channel passthrough', () => {
  // No exposed function should forward an arbitrary channel/payload.
  assert.doesNotMatch(preload, /invoke\(\s*channel/);
  assert.doesNotMatch(preload, /exposeInMainWorld\([^)]*ipcRenderer\s*\)/);
  assert.match(preload, /Object\.freeze\(/);
});

test('every ipcRenderer.invoke target is a literal msb: channel', () => {
  const targets = [...preload.matchAll(/invoke\(\s*'([^']+)'/g)].map((m) => m[1]);
  assert.ok(targets.length >= 11);
  for (const t of targets) assert.match(t, /^msb:[a-zA-Z]+$/);
});

test('renderer never uses innerHTML with interpolation and has no inline handlers', () => {
  // The only innerHTML in app.js is the code-defined static-string branch of el().
  const bad = [...rendererApp.matchAll(/\.innerHTML\s*=\s*`[^`]*\$\{/g)];
  assert.equal(bad.length, 0, 'template-literal innerHTML with ${...} found');
  const inlineHandler = /\son(click|change|input|submit|load|error|keydown|keyup|mouseover|mouseout|focus|blur)\s*=/i;
  assert.doesNotMatch(rendererHtml, inlineHandler);
  assert.doesNotMatch(rendererApp, inlineHandler);
  assert.doesNotMatch(rendererApp, /setAttribute\(\s*['"]on/);
});

test('renderer has no direct network access', () => {
  assert.doesNotMatch(rendererApp, /\bfetch\(|XMLHttpRequest|WebSocket|EventSource|import\(/);
});

test('bridge talks to loopback only', () => {
  assert.match(bridge, /http:\/\/\$\{host\}:\$\{this\.port\}/);
  assert.doesNotMatch(bridge, /https?:\/\/(?!\$\{)/); // no hard-coded external URL
});
