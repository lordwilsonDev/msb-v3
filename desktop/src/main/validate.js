/**
 * MSB v3 Desktop - IPC input validation.
 *
 * Every IPC channel validates its payload here BEFORE the bridge is called.
 * Validation is allow-list shaped: unknown fields are ignored, malformed
 * fields are rejected with a typed error. Renderer content is untrusted.
 *
 * Each validator returns either:
 *   { ok: true, value: <normalised args> }
 *   { ok: false, error: 'IPC_VALIDATION_FAILED', detail: <why> }
 */

'use strict';

const MAX_ID_LEN = 200;
const MAX_SESSION_LEN = 200;
const MAX_QUERY_LEN = 2000;
const MAX_LIMIT = 500;
const APPROVE_ACTIONS = Object.freeze(['approve', 'reject']);
const KILLSWITCH_OPS = Object.freeze(['arm', 'disarm']);

const CONTROL_CHARS = new RegExp('[\u0000-\u001F\u007F]');
const SESSION_ALLOWED = /^[A-Za-z0-9_.:-]+$/;

function fail(detail) {
  return { ok: false, error: 'IPC_VALIDATION_FAILED', detail };
}

function ok(value) {
  return { ok: true, value };
}

/** A non-empty string within `max` chars and free of control characters. */
function cleanString(v, max) {
  if (typeof v !== 'string') return null;
  const s = v.trim();
  if (!s || s.length > max) return null;
  if (CONTROL_CHARS.test(s)) return null;
  return s;
}

/** An integer in [1, max]. undefined/null -> `fallback`. */
function cleanLimit(v, fallback, max) {
  if (v === undefined || v === null) return fallback;
  if (typeof v !== 'number' || !Number.isInteger(v)) return null;
  if (v < 1 || v > max) return null;
  return v;
}

const validators = {
  attach(payload) {
    const p = payload && typeof payload === 'object' ? payload : {};
    const out = {};
    if (p.host !== undefined) {
      if (p.host !== '127.0.0.1' && p.host !== 'localhost') {
        return fail('host must be 127.0.0.1 or localhost');
      }
      out.host = p.host;
    }
    if (p.port !== undefined) {
      const port = typeof p.port === 'string' ? Number(p.port) : p.port;
      if (!Number.isInteger(port) || port < 1 || port > 65535) {
        return fail('port out of range');
      }
      out.port = String(port);
    }
    return ok(out);
  },

  approve(payload) {
    const p = payload && typeof payload === 'object' ? payload : {};
    const id = cleanString(p.id, MAX_ID_LEN);
    if (!id) return fail('id must be a non-empty string');
    if (!APPROVE_ACTIONS.includes(p.action)) {
      return fail(`action must be one of ${APPROVE_ACTIONS.join(', ')}`);
    }
    const reason = p.reason === undefined ? undefined : cleanString(p.reason, MAX_QUERY_LEN);
    if (p.reason !== undefined && reason === null) return fail('reason malformed');
    return ok({ id, action: p.action, reason });
  },

  killswitchSet(payload) {
    const p = payload && typeof payload === 'object' ? payload : {};
    if (!KILLSWITCH_OPS.includes(p.op)) {
      return fail(`op must be one of ${KILLSWITCH_OPS.join(', ')}`);
    }
    const reason = p.reason === undefined ? undefined : cleanString(p.reason, MAX_QUERY_LEN);
    if (p.reason !== undefined && reason === null) return fail('reason malformed');
    return ok({ op: p.op, reason });
  },

  memory(payload) {
    const p = payload && typeof payload === 'object' ? payload : {};
    const session = p.session === undefined ? 'default' : cleanString(p.session, MAX_SESSION_LEN);
    if (!session) return fail('session malformed');
    if (!SESSION_ALLOWED.test(session)) return fail('session has illegal characters');
    const limit = cleanLimit(p.limit, 50, MAX_LIMIT);
    if (limit === null) return fail('limit out of range');
    return ok({ session, limit });
  },

  search(payload) {
    const p = payload && typeof payload === 'object' ? payload : {};
    const query = cleanString(p.query, MAX_QUERY_LEN);
    if (!query) return fail('query must be a non-empty string');
    const limit = cleanLimit(p.limit, 10, 100);
    if (limit === null) return fail('limit out of range');
    return ok({ query, limit });
  },
};

/**
 * Validate a payload for `channel`. Channels with no declared validator
 * (health, identity, cockpit, approvals, killswitch) take no arguments and
 * always pass with an empty value.
 */
function validate(channel, payload) {
  const fn = validators[channel];
  if (!fn) return ok({});
  return fn(payload);
}

module.exports = {
  validate,
  validators,
  APPROVE_ACTIONS,
  KILLSWITCH_OPS,
  MAX_QUERY_LEN,
  MAX_LIMIT,
};
