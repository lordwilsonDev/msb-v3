/**
 * MSB v3 Desktop - visible-only poller.
 *
 * Calls `load()` now and then every `intervalMs` while running and the
 * document is visible; after a failed load it waits `backoffMs` instead.
 * While hidden it neither loads nor reschedules; `wake()` (wire it to
 * `visibilitychange`) resumes it. Timers are injectable so Node tests can
 * drive it without a DOM.
 *
 * Browser: window.MsbPoller.createPoller. Node: module.exports.createPoller.
 */

(function (root) {
  'use strict';

  function createPoller({
    load,
    intervalMs,
    backoffMs = 30000,
    isVisible,
    onResult = () => {},
    setTimer = setTimeout,
    clearTimer = clearTimeout,
  }) {
    let timer = null;
    let running = false;
    let inFlight = false;

    async function tick() {
      timer = null;
      if (!running || !isVisible()) return; // paused until wake()
      inFlight = true;
      let result;
      try {
        result = await load();
      } catch (err) {
        result = { ok: false, error: String((err && err.message) || err) };
      }
      inFlight = false;
      onResult(result);
      if (running) timer = setTimer(tick, result && result.ok ? intervalMs : backoffMs);
    }

    return {
      start() {
        if (running) return;
        running = true;
        tick();
      },
      stop() {
        running = false;
        if (timer) clearTimer(timer);
        timer = null;
      },
      wake() {
        if (running && !timer && !inFlight && isVisible()) tick();
      },
      get running() {
        return running;
      },
    };
  }

  if (typeof module !== 'undefined' && module.exports) module.exports = { createPoller };
  else root.MsbPoller = Object.freeze({ createPoller });
})(this);
