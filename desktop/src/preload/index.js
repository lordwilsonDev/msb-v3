/**
 * MSB v3 Desktop — Preload Script
 *
 * Runs in an isolated context (contextIsolation: true).
 * Exposes a typed, minimal API to the renderer via contextBridge.
 *
 * Security model:
 *   - nodeIntegration: false (no Node.js in renderer)
 *   - contextIsolation: true (preload context ≠ renderer context)
 *   - sandbox: true (renderer runs in OS sandbox)
 *   - Only explicitly exposed APIs are available to renderer
 *   - Every IPC call is a named method (no arbitrary channel access)
 */

'use strict';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('msb', {
  /**
   * Attach to the msb-v3 server.
   * @param {object} opts - { host?: string, port?: string }
   * @returns {Promise<{ok: boolean, health?: object, identity?: object, error?: string}>}
   */
  attach: (opts) => ipcRenderer.invoke('msb:attach', opts || {}),

  /**
   * Check server health.
   * @returns {Promise<{ok: boolean, service?: string, version?: string, error?: string}>}
   */
  health: () => ipcRenderer.invoke('msb:health'),

  /**
   * Get cockpit dashboard data.
   * @returns {Promise<object>}
   */
  cockpit: () => ipcRenderer.invoke('msb:cockpit'),

  /**
   * Get pending approvals.
   * @returns {Promise<{approvals?: Array, error?: string}>}
   */
  approvals: () => ipcRenderer.invoke('msb:approvals'),

  /**
   * Approve or reject a pending action.
   * @param {string} id - Approval ID
   * @param {'approve'|'reject'} action
   * @returns {Promise<{ok: boolean, error?: string}>}
   */
  approve: (id, action) => ipcRenderer.invoke('msb:approve', { id, action }),

  /**
   * Get kill switch status.
   * @returns {Promise<{switches?: Array, error?: string}>}
   */
  killswitch: () => ipcRenderer.invoke('msb:killswitch'),

  /**
   * Get conversation memory for a session.
   * @param {string} session
   * @param {number} [limit]
   * @returns {Promise<{messages?: Array, error?: string}>}
   */
  memory: (session, limit) => ipcRenderer.invoke('msb:memory', { session, limit }),

  /**
   * Search the vault (semantic search via RAG).
   * @param {string} query
   * @param {number} [limit]
   * @returns {Promise<{results?: Array, error?: string}>}
   */
  search: (query, limit) => ipcRenderer.invoke('msb:search', { query, limit }),
});
