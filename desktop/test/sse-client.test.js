'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const { TaskStreamClient } = require('../src/main/sse-client');

/** A throwaway server that answers each connection with a scripted responder. */
function fakeSseServer(onRequest) {
  const seen = [];
  const server = http.createServer((req, res) => {
    seen.push({ method: req.method, url: req.url, headers: req.headers });
    onRequest(req, res, seen.length);
  });
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address();
      resolve({ server, port, seen, close: () => new Promise((r) => server.close(r)) });
    });
  });
}

test('does not connect and emits an error when no operator token is configured', async () => {
  const m = await fakeSseServer(() => {
    throw new Error('should never be called');
  });
  const client = new TaskStreamClient(`http://127.0.0.1:${m.port}`, '');
  const errors = [];
  client.on('error', (e) => errors.push(e));
  client.open('t1');
  await new Promise((r) => setTimeout(r, 50));
  await m.close();
  assert.equal(m.seen.length, 0);
  assert.equal(errors.length, 1);
  assert.equal(errors[0].taskId, 't1');
  assert.match(errors[0].error, /OPERATOR_TOKEN_NOT_CONFIGURED/);
});

test('sends the bearer token and requests the observations/stream path', async () => {
  const m = await fakeSseServer((req, res) => {
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    res.write('event: observation\ndata: {"source":"worker","observed_at":"t0"}\n\n');
    // leave open; test closes the client explicitly
  });
  const client = new TaskStreamClient(`http://127.0.0.1:${m.port}`, 'SECRET-OP');
  const events = [];
  client.on('event', (e) => events.push(e));
  client.open('task-abc');
  await new Promise((r) => setTimeout(r, 50));
  client.close('task-abc');
  await m.close();
  assert.equal(m.seen[0].url, '/agent/tasks/task-abc/observations/stream');
  assert.equal(m.seen[0].headers.authorization, 'Bearer SECRET-OP');
  assert.equal(events.length, 1);
  assert.deepEqual(events[0].data, { source: 'worker', observed_at: 't0' });
  assert.equal(events[0].event, 'observation');
});

test('ignores heartbeat comment lines', async () => {
  const m = await fakeSseServer((req, res) => {
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    res.write(': heartbeat\n\n');
    res.write('event: observation\ndata: {"source":"x","observed_at":"t1"}\n\n');
  });
  const client = new TaskStreamClient(`http://127.0.0.1:${m.port}`, 'tok');
  const events = [];
  client.on('event', (e) => events.push(e));
  client.open('t2');
  await new Promise((r) => setTimeout(r, 50));
  client.close('t2');
  await m.close();
  assert.equal(events.length, 1);
  assert.equal(events[0].data.source, 'x');
});

test('emits done and stops on a terminal frame (no reconnect after)', async () => {
  const m = await fakeSseServer((req, res) => {
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    res.write('event: done\ndata: {"state":"COMPLETED"}\n\n');
    res.end();
  });
  const client = new TaskStreamClient(`http://127.0.0.1:${m.port}`, 'tok', { backoffMs: [10, 10] });
  const events = [];
  const reconnects = [];
  client.on('event', (e) => events.push(e));
  client.on('reconnecting', (e) => reconnects.push(e));
  client.open('t3');
  await new Promise((r) => setTimeout(r, 100));
  await m.close();
  assert.equal(events.length, 1);
  assert.equal(events[0].event, 'done');
  assert.deepEqual(events[0].data, { state: 'COMPLETED' });
  assert.equal(reconnects.length, 0, 'a done frame must not trigger a reconnect');
});

test('reconnects with backoff after a dropped connection, then succeeds', async () => {
  const m = await fakeSseServer((req, res, n) => {
    if (n === 1) {
      res.writeHead(200, { 'Content-Type': 'text/event-stream' });
      res.write('event: observation\ndata: {"source":"a","observed_at":"t0"}\n\n');
      res.destroy(); // simulate a dropped connection mid-stream
      return;
    }
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    res.write('event: observation\ndata: {"source":"b","observed_at":"t1"}\n\n');
  });
  const client = new TaskStreamClient(`http://127.0.0.1:${m.port}`, 'tok', { backoffMs: [10, 20] });
  const events = [];
  const reconnects = [];
  client.on('event', (e) => events.push(e));
  client.on('reconnecting', (e) => reconnects.push(e));
  client.open('t4');
  await new Promise((r) => setTimeout(r, 200));
  client.close('t4');
  await m.close();
  assert.ok(reconnects.length >= 1, 'expected at least one reconnect attempt');
  assert.equal(reconnects[0].attempt, 1);
  assert.ok(events.some((e) => e.data.source === 'a'));
  assert.ok(events.some((e) => e.data.source === 'b'));
});

test('gives up and emits a terminal error after exhausting retries', async () => {
  const m = await fakeSseServer((req, res) => {
    res.writeHead(500, { 'Content-Type': 'application/json' });
    res.end('{}');
  });
  const client = new TaskStreamClient(`http://127.0.0.1:${m.port}`, 'tok', { backoffMs: [5, 5] });
  const errors = [];
  client.on('error', (e) => errors.push(e));
  client.open('t5');
  await new Promise((r) => setTimeout(r, 100));
  await m.close();
  assert.equal(errors.length, 1);
  assert.match(errors[0].error, /RECONNECT_EXHAUSTED/);
});

test('closeAll stops every open stream', async () => {
  const m = await fakeSseServer((req, res) => {
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    // never write anything further; connection just stays open
  });
  const client = new TaskStreamClient(`http://127.0.0.1:${m.port}`, 'tok');
  client.open('a');
  client.open('b');
  await new Promise((r) => setTimeout(r, 30));
  client.closeAll();
  // closing twice must not throw
  assert.doesNotThrow(() => client.close('a'));
  await m.close();
});
