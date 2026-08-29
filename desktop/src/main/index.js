/**
 * MSB v3 Desktop - Main Process
 *
 * Hardened Electron shell and typed IPC boundary. The main process:
 *   1. Creates a locked-down BrowserWindow (no Node in renderer, sandboxed,
 *      context-isolated, navigation-restricted, CSP-enforced).
 *   2. Holds all secrets (operator token, MCP secret, RAG key) - these never
 *      cross into the renderer.
 *   3. Validates every IPC payload, then calls the attach-only bridge.
 *
 * Process ownership: launchd (com.lordwilson.msb-v3) supervises the runtime.
 * This app is a CLIENT. It discovers -> health-checks -> verifies identity ->
 * attaches. It never spawns, kills, or restarts msb-v3.
 */

'use strict';

const { app, BrowserWindow, ipcMain, session, shell } = require('electron');
const path = require('path');
const { MsbBridge } = require('./bridge');
const { validate } = require('./validate');

// --- config (main process only) -------------------------------------------

const MSB_HOST = process.env.MSB_HOST || '127.0.0.1';
const MSB_PORT = process.env.MSB_PORT || '8766';

const SECRETS = {
  operatorToken: process.env.MSB_OPERATOR_TOKEN || '',
  mcpSecret: process.env.MCP_BRIDGE_SECRET || '',
  ragApiKey: process.env.MSB_RAG_API_KEY || '',
  ragTenant: process.env.MSB_RAG_TENANT || 'wilson-vault',
};

const IS_DEV = process.env.NODE_ENV === 'development';
const DEV_URL = 'http://localhost:5173';
const RENDERER_FILE = path.join(__dirname, '..', 'renderer', 'index.html');

const CSP = [
  "default-src 'none'",
  "script-src 'self'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data:",
  "connect-src 'none'",
  "base-uri 'none'",
  "form-action 'none'",
  "frame-ancestors 'none'",
].join('; ');

let mainWindow = null;
let bridge = null;
let onWindow = null;

/** Register a callback invoked with the BrowserWindow after it is created.
 *  Used by dev/smoke scripts; must be called synchronously at require time. */
function setOnWindow(fn) {
  onWindow = typeof fn === 'function' ? fn : null;
}

// --- window --------------------------------------------------------------

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    title: 'MSB v3 - Sovereign Operations',
    backgroundColor: '#0a0a0a',
    webPreferences: {
      preload: path.join(__dirname, '..', 'preload', 'index.js'),
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      webviewTag: false,
      allowRunningInsecureContent: false,
      experimentalFeatures: false,
      devTools: IS_DEV,
    },
  });

  hardenContents(mainWindow.webContents);

  if (IS_DEV) {
    mainWindow.loadURL(DEV_URL);
  } else {
    mainWindow.loadFile(RENDERER_FILE);
  }

  mainWindow.on('closed', () => {
    mainWindow = null;
  });

  if (onWindow) onWindow(mainWindow);
}

/**
 * Lock down a webContents: no new windows, no navigation away from the
 * renderer entrypoint, no webview attach, no permission grants.
 */
function hardenContents(contents) {
  contents.setWindowOpenHandler(({ url }) => {
    // External links open in the OS browser; nothing opens in-app.
    if (/^https?:\/\//.test(url)) shell.openExternal(url).catch(() => {});
    return { action: 'deny' };
  });

  contents.on('will-navigate', (event, url) => {
    const allowed = IS_DEV ? url.startsWith(DEV_URL) : url.startsWith('file://');
    if (!allowed) event.preventDefault();
  });

  contents.on('will-redirect', (event) => event.preventDefault());
  contents.on('will-attach-webview', (event) => event.preventDefault());
}

// --- IPC ---------------------------------------------------------------

/**
 * Register one IPC channel. Validates the payload, guards the bridge,
 * calls `handler`, and guarantees a typed { ok, ... } result - never throws
 * to the renderer.
 */
function channel(name, handler, { needsBridge = true } = {}) {
  ipcMain.handle(`msb:${name}`, async (_event, payload) => {
    const v = validate(name, payload);
    if (!v.ok) return { ok: false, error: v.error, detail: v.detail };
    if (needsBridge && !bridge) return { ok: false, error: 'NOT_ATTACHED' };
    try {
      return await handler(v.value);
    } catch (err) {
      return { ok: false, error: 'HANDLER_FAILURE', detail: String((err && err.message) || err) };
    }
  });
}

function registerIpc() {
  channel(
    'attach',
    async (args) => {
      bridge = new MsbBridge(args.host || MSB_HOST, args.port || MSB_PORT, SECRETS);
      const health = await bridge.health();
      if (!health.ok) {
        return { ok: false, error: 'MSB_UNAVAILABLE', detail: health.error, state: 'OFFLINE' };
      }
      const identity = await bridge.identity();
      if (!identity.ok) {
        return { ok: true, state: 'DEGRADED', health: health.data, identity: null, detail: identity.error };
      }
      if (!identity.data.expected) {
        return {
          ok: false,
          error: 'WRONG_RUNTIME',
          detail: `expected service "msb-v3", got "${identity.data.service}"`,
          state: 'BLOCKED',
        };
      }
      return {
        ok: true,
        state: identity.data.ready ? 'READY' : 'DEGRADED',
        health: health.data,
        identity: identity.data,
        operator: bridge.hasOperatorToken(),
      };
    },
    { needsBridge: false }
  );

  channel('health', () => bridge.health());
  channel('identity', () => bridge.identity());
  channel('cockpit', () => bridge.cockpit());
  channel('governanceStatus', () => bridge.governanceStatus());
  channel('approvals', () => bridge.approvals());
  channel('approve', (a) => bridge.approve(a.id, a.action, a.reason));
  channel('killswitch', () => bridge.governanceStatus());
  channel('killswitchSet', (a) => bridge.killswitchSet(a.op, a.reason));
  channel('memory', (a) => bridge.memory(a.session, a.limit));
  channel('search', (a) => bridge.search(a.query, a.limit));
}

// --- lifecycle -------------------------------------------------------

app.enableSandbox();

app.on('web-contents-created', (_event, contents) => hardenContents(contents));

app.whenReady().then(() => {
  // Belt-and-braces CSP on top of the renderer <meta> tag, and deny every
  // permission request (camera, geolocation, notifications, ...).
  if (!IS_DEV) {
    session.defaultSession.webRequest.onHeadersReceived((details, cb) => {
      cb({ responseHeaders: { ...details.responseHeaders, 'Content-Security-Policy': [CSP] } });
    });
  }
  session.defaultSession.setPermissionRequestHandler((_wc, _perm, cb) => cb(false));
  session.defaultSession.setPermissionCheckHandler(() => false);

  bridge = new MsbBridge(MSB_HOST, MSB_PORT, SECRETS);
  registerIpc();
  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  // Closing the cockpit never touches the runtime - launchd keeps it alive.
  if (process.platform !== 'darwin') app.quit();
});

module.exports = { CSP, setOnWindow };
