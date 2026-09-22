'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { createPoller } = require('../src/renderer/poller');

function fakeTimers() {
  const pending = [];
  return {
    pending,
    setTimer: (fn, ms) => {
      const t = { fn, ms };
      pending.push(t);
      return t;
    },
    clearTimer: (t) => {
      const i = pending.indexOf(t);
      if (i >= 0) pending.splice(i, 1);
    },
    async fire() {
      const t = pending.shift();
      await t.fn();
    },
  };
}

const flush = () => new Promise((r) => setImmediate(r));

function setup({ visible = true, result = { ok: true } } = {}) {
  const timers = fakeTimers();
  const calls = [];
  const view = { visible };
  const results = [];
  const poller = createPoller({
    load: async () => {
      calls.push(1);
      return typeof result === 'function' ? result() : result;
    },
    intervalMs: 5000,
    backoffMs: 30000,
    isVisible: () => view.visible,
    onResult: (r) => results.push(r),
    setTimer: timers.setTimer,
    clearTimer: timers.clearTimer,
  });
  return { poller, timers, calls, view, results };
}

test('loads immediately, then every intervalMs while visible', async () => {
  const { poller, timers, calls } = setup();
  poller.start();
  await flush();
  assert.equal(calls.length, 1);
  assert.equal(timers.pending.length, 1);
  assert.equal(timers.pending[0].ms, 5000);
  await timers.fire();
  assert.equal(calls.length, 2);
});

test('a failed load backs off to backoffMs', async () => {
  const { poller, timers, results } = setup({ result: { ok: false, error: 'MSB_UNAVAILABLE' } });
  poller.start();
  await flush();
  assert.equal(timers.pending[0].ms, 30000);
  assert.equal(results[0].ok, false);
});

test('a thrown load is reported as a failed result and backs off', async () => {
  const { poller, timers, results } = setup({
    result: () => {
      throw new Error('boom');
    },
  });
  poller.start();
  await flush();
  assert.equal(results[0].ok, false);
  assert.match(results[0].error, /boom/);
  assert.equal(timers.pending[0].ms, 30000);
});

test('does not load while hidden; wake() resumes when visible', async () => {
  const { poller, timers, calls, view } = setup({ visible: false });
  poller.start();
  await flush();
  assert.equal(calls.length, 0);
  assert.equal(timers.pending.length, 0);
  view.visible = true;
  poller.wake();
  await flush();
  assert.equal(calls.length, 1);
});

test('going hidden stops rescheduling', async () => {
  const { poller, timers, calls, view } = setup();
  poller.start();
  await flush();
  view.visible = false;
  await timers.fire();
  assert.equal(calls.length, 1);
  assert.equal(timers.pending.length, 0);
});

test('stop() clears the timer and ends polling', async () => {
  const { poller, timers, calls } = setup();
  poller.start();
  await flush();
  poller.stop();
  assert.equal(timers.pending.length, 0);
  assert.equal(poller.running, false);
  poller.wake();
  await flush();
  assert.equal(calls.length, 1);
});

test('start() twice does not double-poll', async () => {
  const { poller, calls } = setup();
  poller.start();
  poller.start();
  await flush();
  assert.equal(calls.length, 1);
});
