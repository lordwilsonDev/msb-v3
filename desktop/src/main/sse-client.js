/**
 * MSB v3 Desktop - SSE task-stream client (main process only).
 *
 * Opens GET /agent/tasks/{task_id}/observations/stream per task, parses SSE
 * frames, and forwards them as events. The renderer never sees this module
 * or a raw EventSource - see security.test.js "renderer has no direct
 * network access". Fail-closed like the bridge: no operator token, no
 * request.
 */

'use strict';

const http = require('http');
const { EventEmitter } = require('events');

const DEFAULT_BACKOFF_MS = [1000, 2000, 4000, 8000, 16000];

class TaskStreamClient extends EventEmitter {
  /**
   * @param {string} baseUrl - e.g. "http://127.0.0.1:8766"
   * @param {string} operatorToken
   * @param {{backoffMs?: number[]}} [opts]
   */
  constructor(baseUrl, operatorToken, opts = {}) {
    super();
    this.baseUrl = baseUrl;
    this.operatorToken = operatorToken;
    this._backoff = opts.backoffMs || DEFAULT_BACKOFF_MS;
    this._streams = new Map(); // taskId -> { req, closed, attempt, timer }
  }

  /** Start (or no-op if already open) a live stream for one task. */
  open(taskId) {
    if (this._streams.has(taskId)) return;
    if (!this.operatorToken) {
      this.emit('error', { taskId, error: 'OPERATOR_TOKEN_NOT_CONFIGURED' });
      return;
    }
    this._streams.set(taskId, { req: null, closed: false, attempt: 0, timer: null });
    this._connect(taskId);
  }

  /** Stop a task's stream. Idempotent - safe to call more than once. */
  close(taskId) {
    const entry = this._streams.get(taskId);
    if (!entry) return;
    entry.closed = true;
    if (entry.timer) clearTimeout(entry.timer);
    if (entry.req) entry.req.destroy();
    this._streams.delete(taskId);
  }

  /** Stop every open stream (e.g. on window close). */
  closeAll() {
    for (const taskId of [...this._streams.keys()]) this.close(taskId);
  }

  _connect(taskId) {
    const entry = this._streams.get(taskId);
    if (!entry || entry.closed) return;

    const path = `/agent/tasks/${encodeURIComponent(taskId)}/observations/stream`;
    let buffer = '';

    const req = http.request(
      this.baseUrl + path,
      {
        method: 'GET',
        headers: { Accept: 'text/event-stream', Authorization: `Bearer ${this.operatorToken}` },
      },
      (res) => {
        if (res.statusCode !== 200) {
          res.resume();
          return this._scheduleReconnect(taskId, `HTTP_${res.statusCode}`);
        }
        entry.attempt = 0; // connected - reset backoff
        res.setEncoding('utf8');
        res.on('data', (chunk) => {
          buffer += chunk;
          let idx;
          while ((idx = buffer.indexOf('\n\n')) !== -1) {
            const raw = buffer.slice(0, idx);
            buffer = buffer.slice(idx + 2);
            this._handleMessage(taskId, raw);
          }
        });
        res.on('end', () => this._scheduleReconnect(taskId, 'STREAM_ENDED'));
      }
    );
    req.on('error', (err) => this._scheduleReconnect(taskId, `MSB_UNREACHABLE: ${err.message}`));
    req.end();
    entry.req = req;
  }

  _handleMessage(taskId, raw) {
    let eventName = 'message';
    let dataLine = '';
    for (const line of raw.split('\n')) {
      if (line.startsWith(':')) continue; // heartbeat/comment - no data
      if (line.startsWith('event:')) eventName = line.slice(6).trim();
      else if (line.startsWith('data:')) dataLine += line.slice(5).trim();
    }
    if (!dataLine) return;
    let data;
    try {
      data = JSON.parse(dataLine);
    } catch {
      return; // malformed frame - drop, don't crash the stream
    }
    this.emit('event', { taskId, event: eventName, data });
    if (eventName === 'done') this.close(taskId);
  }

  _scheduleReconnect(taskId, reason) {
    const entry = this._streams.get(taskId);
    if (!entry || entry.closed) return;
    if (entry.attempt >= this._backoff.length) {
      this.emit('error', { taskId, error: `RECONNECT_EXHAUSTED: ${reason}` });
      this.close(taskId);
      return;
    }
    const delayMs = this._backoff[entry.attempt];
    entry.attempt += 1;
    this.emit('reconnecting', { taskId, attempt: entry.attempt, delayMs, reason });
    entry.timer = setTimeout(() => this._connect(taskId), delayMs);
  }
}

module.exports = { TaskStreamClient };
