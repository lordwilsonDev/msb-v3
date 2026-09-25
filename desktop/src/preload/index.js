/**
 * MSB v3 Desktop - Preload
 *
 * Runs in an isolated world (contextIsolation: true). Exposes a small, frozen,
 * domain-specific API on window.msb. There is NO generic channel access:
 * every method maps to one named IPC handler in the main process, which
 * re-validates the payload. Renderer args are coerced to safe primitives
 * here as a first pass; the main process is the authority.
 */

'use strict';

const { contextBridge, ipcRenderer } = require('electron');

const str = (v) => (v === undefined || v === null ? undefined : String(v));
const int = (v) => {
  if (v === undefined || v === null) return undefined;
  const n = Number(v);
  return Number.isFinite(n) ? Math.trunc(n) : undefined;
};

const api = Object.freeze({
  /** Discover -> health -> identity -> attach. Returns runtime state. */
  attach: (opts) => {
    const o = opts && typeof opts === 'object' ? opts : {};
    return ipcRenderer.invoke('msb:attach', { host: str(o.host), port: str(o.port) });
  },

  /** GET /health */
  health: () => ipcRenderer.invoke('msb:health'),

  /** GET /status - runtime identity (service, version, ready, model). */
  identity: () => ipcRenderer.invoke('msb:identity'),

  /** GET /cockpit/api - aggregated dashboard state. */
  cockpit: () => ipcRenderer.invoke('msb:cockpit'),

  /** GET /governance/status - killswitch + budgets + approvals summary. */
  governanceStatus: () => ipcRenderer.invoke('msb:governanceStatus'),

  /** GET /governance/approvals - pending queue. */
  approvals: () => ipcRenderer.invoke('msb:approvals'),

  /**
   * Approve or reject one pending action (operator token required in main).
   * @param {string} id
   * @param {'approve'|'reject'} action
   * @param {string} [reason]
   */
  approve: (id, action, reason) =>
    ipcRenderer.invoke('msb:approve', { id: str(id), action: str(action), reason: str(reason) }),

  /** GET /governance/status, killswitch view. */
  killswitch: () => ipcRenderer.invoke('msb:killswitch'),

  /**
   * Arm or disarm the kill switch (operator token required in main).
   * @param {'arm'|'disarm'} op
   * @param {string} [reason]
   */
  killswitchSet: (op, reason) =>
    ipcRenderer.invoke('msb:killswitchSet', { op: str(op), reason: str(reason) }),

  /**
   * GET /memory/{session} - execution/evidence memory.
   * @param {string} [session]
   * @param {number} [limit]
   */
  memory: (session, limit) =>
    ipcRenderer.invoke('msb:memory', { session: str(session), limit: int(limit) }),

  /**
   * POST /rag/search - vault knowledge search.
   * @param {string} query
   * @param {number} [limit]
   */
  search: (query, limit) =>
    ipcRenderer.invoke('msb:search', { query: str(query), limit: int(limit) }),

  /**
   * GET /agent/tasks - recent/in-flight unified tasks.
   * @param {number} [limit]
   */
  listTasks: (limit) => ipcRenderer.invoke('msb:listTasks', { limit: int(limit) }),

  /** GET /ops/background - watch-only snapshot of background subsystems. */
  background: () => ipcRenderer.invoke('msb:background'),

  /** GET /cron/jobs - scheduled jobs. */
  cronJobs: () => ipcRenderer.invoke('msb:cronJobs'),

  /**
   * GET /cron/jobs/{id}/history - recent runs of one job.
   * @param {string} jobId
   * @param {number} [limit]
   */
  cronHistory: (jobId, limit) =>
    ipcRenderer.invoke('msb:cronHistory', { jobId: str(jobId), limit: int(limit) }),

  /** GET /plei/calibrate - calibration report. */
  pleiCalibrate: () => ipcRenderer.invoke('msb:pleiCalibrate'),

  /**
   * GET /cockpit/audit - recent governed-run receipts.
   * @param {number} [limit]
   */
  auditStream: (limit) => ipcRenderer.invoke('msb:auditStream', { limit: int(limit) }),

  /** Start a live observation stream for one task. @param {string} taskId */
  subscribeTask: (taskId) => ipcRenderer.invoke('msb:subscribeTask', { taskId: str(taskId) }),

  /** Stop a task's live stream. @param {string} taskId */
  unsubscribeTask: (taskId) => ipcRenderer.invoke('msb:unsubscribeTask', { taskId: str(taskId) }),

  /**
   * POST /chat - send a message, get a model response. Persists to the
   * session's memory - call memory(session) afterward to see the exchange.
   * @param {string} query
   * @param {string} [session]
   */
  sendChat: (query, session) =>
    ipcRenderer.invoke('msb:sendChat', { query: str(query), session: str(session) }),

  /**
   * Subscribe to task-stream events (observation/done/reconnecting/stream-error)
   * for every task currently subscribed via subscribeTask. Filter by
   * event.taskId in the callback - this is one shared channel, not per-task.
   * @param {(event: {taskId: string, event: string, data: object}) => void} callback
   * @returns {() => void} call to unsubscribe
   */
  onTaskEvent: (callback) => {
    const listener = (_event, payload) => callback(payload);
    ipcRenderer.on('msb:taskEvent', listener);
    return () => ipcRenderer.removeListener('msb:taskEvent', listener);
  },
});

contextBridge.exposeInMainWorld('msb', api);
