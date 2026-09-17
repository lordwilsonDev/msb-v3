'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { validate } = require('../src/main/validate');

test('unknown channel passes with empty value', () => {
  const r = validate('health', undefined);
  assert.equal(r.ok, true);
  assert.deepEqual(r.value, {});
});

test('approve: rejects missing id', () => {
  const r = validate('approve', { action: 'approve' });
  assert.equal(r.ok, false);
  assert.equal(r.error, 'IPC_VALIDATION_FAILED');
});

test('approve: rejects non-string id', () => {
  assert.equal(validate('approve', { id: 12, action: 'approve' }).ok, false);
  assert.equal(validate('approve', { id: {}, action: 'approve' }).ok, false);
});

test('approve: rejects action outside allow-list', () => {
  for (const action of ['cancel', 'delete', 'rm -rf', '', 'APPROVE', undefined]) {
    assert.equal(validate('approve', { id: 'x', action }).ok, false, `action=${action}`);
  }
});

test('approve: accepts a well-formed payload', () => {
  const r = validate('approve', { id: 'appr-1', action: 'reject', reason: 'not now' });
  assert.equal(r.ok, true);
  assert.deepEqual(r.value, { id: 'appr-1', action: 'reject', reason: 'not now' });
});

test('approve: rejects control chars in id', () => {
  const nul = String.fromCharCode(0);
  const bell = String.fromCharCode(7);
  assert.equal(validate('approve', { id: `a${nul}b`, action: 'approve' }).ok, false);
  assert.equal(validate('approve', { id: `a${bell}b`, action: 'approve' }).ok, false);
  assert.equal(validate('approve', { id: 'a\nb', action: 'approve' }).ok, false);
});

test('killswitchSet: only arm/disarm', () => {
  assert.equal(validate('killswitchSet', { op: 'arm' }).ok, true);
  assert.equal(validate('killswitchSet', { op: 'disarm' }).ok, true);
  assert.equal(validate('killswitchSet', { op: 'nuke' }).ok, false);
  assert.equal(validate('killswitchSet', {}).ok, false);
});

test('memory: session path-traversal and illegal chars rejected', () => {
  const bad = ['../etc', 'a/b', 'a b', 'a;b', 'a$b', `a${String.fromCharCode(0)}`];
  for (const session of bad) {
    assert.equal(validate('memory', { session }).ok, false, `session=${JSON.stringify(session)}`);
  }
});

test('memory: defaults and bounds', () => {
  assert.deepEqual(validate('memory', {}).value, { session: 'default', limit: 50 });
  assert.equal(validate('memory', { session: 'ok', limit: 0 }).ok, false);
  assert.equal(validate('memory', { session: 'ok', limit: 99999 }).ok, false);
  assert.equal(validate('memory', { session: 'ok', limit: 1.5 }).ok, false);
  assert.equal(validate('memory', { session: 'ok', limit: 25 }).value.limit, 25);
});

test('search: empty / oversized query rejected', () => {
  assert.equal(validate('search', { query: '' }).ok, false);
  assert.equal(validate('search', { query: '   ' }).ok, false);
  assert.equal(validate('search', { query: 'x'.repeat(5000) }).ok, false);
  assert.equal(validate('search', { query: 'real question', limit: 5 }).value.limit, 5);
});

test('attach: rejects non-loopback host and bad port', () => {
  assert.equal(validate('attach', { host: 'evil.example.com' }).ok, false);
  assert.equal(validate('attach', { host: '10.0.0.5' }).ok, false);
  assert.equal(validate('attach', { port: 70000 }).ok, false);
  assert.equal(validate('attach', { host: '127.0.0.1', port: 8766 }).ok, true);
  assert.equal(validate('attach', {}).ok, true);
});

test('listTasks: defaults limit to 25 and clamps to MAX_LIMIT', () => {
  assert.deepEqual(validate('listTasks', undefined).value, { limit: 25 });
  assert.equal(validate('listTasks', { limit: 501 }).ok, false);
  assert.deepEqual(validate('listTasks', { limit: 5 }).value, { limit: 5 });
});

test('subscribeTask: requires a non-empty taskId', () => {
  assert.equal(validate('subscribeTask', {}).ok, false);
  assert.equal(validate('subscribeTask', { taskId: '' }).ok, false);
  assert.deepEqual(validate('subscribeTask', { taskId: 'task-1' }).value, { taskId: 'task-1' });
});

test('subscribeTask: rejects control characters in taskId', () => {
  const nul = String.fromCharCode(0);
  assert.equal(validate('subscribeTask', { taskId: `a${nul}b` }).ok, false);
});

test('unsubscribeTask: same shape as subscribeTask', () => {
  assert.deepEqual(validate('unsubscribeTask', { taskId: 'task-2' }).value, { taskId: 'task-2' });
  assert.equal(validate('unsubscribeTask', { taskId: 123 }).ok, false);
});

test('sendChat: accepts a well-formed payload', () => {
  const r = validate('sendChat', { query: 'hello there', session: 'sess-1' });
  assert.equal(r.ok, true);
  assert.deepEqual(r.value, { session: 'sess-1', query: 'hello there' });
});

test('sendChat: rejects missing/empty query', () => {
  assert.equal(validate('sendChat', {}).ok, false);
  assert.equal(validate('sendChat', { query: '' }).ok, false);
  assert.equal(validate('sendChat', { query: '   ' }).ok, false);
});

test('sendChat: rejects control characters in query', () => {
  const nul = String.fromCharCode(0);
  assert.equal(validate('sendChat', { query: `hi${nul}there` }).ok, false);
});

test('sendChat: rejects query longer than MAX_CHAT_LEN', () => {
  const { MAX_CHAT_LEN } = require('../src/main/validate');
  assert.equal(validate('sendChat', { query: 'x'.repeat(MAX_CHAT_LEN + 1) }).ok, false);
  assert.equal(validate('sendChat', { query: 'x'.repeat(MAX_CHAT_LEN) }).ok, true);
});

test('sendChat: missing session defaults to "default"', () => {
  assert.deepEqual(validate('sendChat', { query: 'hi' }).value, { session: 'default', query: 'hi' });
});

test('sendChat: session with illegal characters fails', () => {
  const bad = ['../etc', 'a/b', 'a b', 'a;b', 'a$b', `a${String.fromCharCode(0)}`];
  for (const session of bad) {
    assert.equal(validate('sendChat', { query: 'hi', session }).ok, false, `session=${JSON.stringify(session)}`);
  }
});
