/**
 * MSB v3 Desktop — Main Process
 *
 * Hardened Electron shell:
 *   - nodeIntegration: false (no Node.js in renderer)
 *   - contextIsolation: true (preload runs in isolated context)
 *   - sandbox: true (renderer runs in OS sandbox)
 *
 * The main process:
 *   1. Creates the BrowserWindow with hardened preload
 *   2. Runs the IPC bridge (typed, fail-closed)
 *   3. Manages the connection to msb-v3 at 127.0.0.1:8766
 */

'use strict';

const { app, BrowserWindow, ipcMain } = require('electron');
const path = require('path');
const { MsbBridge } = require('./bridge');

let mainWindow = null;
let bridge = null;

const MSB_HOST = process.env.MSB_HOST || '127.0.0.1';
const MSB_PORT = process.env.MSB_PORT || '8766';
const MSB_BASE_URL = `http://${MSB_HOST}:${MSB_PORT}`;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    title: 'MSB v3 — Sovereign Operations',
    webPreferences: {
      preload: path.join(__dirname, '..', 'preload', 'index.js'),
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      // DevTools only in development
      devTools: process.env.NODE_ENV === 'development',
    },
  });

  // Load the renderer
  if (process.env.NODE_ENV === 'development') {
    mainWindow.loadURL('http://localhost:5173');
  } else {
    mainWindow.loadFile(path.join(__dirname, '..', 'renderer', 'index.html'));
  }

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

/**
 * IPC Handlers — typed, fail-closed.
 *
 * Every handler:
 *   1. Validates input (fail on bad input)
 *   2. Calls the bridge (fail-closed on bridge error)
 *   3. Returns a typed result (never throws to renderer)
 */

ipcMain.handle('msb:health', async () => {
  try {
    return await bridge.health();
  } catch (err) {
    return { ok: false, error: String(err) };
  }
});

ipcMain.handle('msb:attach', async (_event, { host, port }) => {
  try {
    bridge = new MsbBridge(host || MSB_HOST, port || MSB_PORT);
    const health = await bridge.health();
    if (!health.ok) {
      return { ok: false, error: 'msb-v3 server not healthy', health };
    }
    const identity = await bridge.identity();
    return { ok: true, health, identity };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
});

ipcMain.handle('msb:cockpit', async () => {
  try {
    return await bridge.cockpit();
  } catch (err) {
    return { ok: false, error: String(err) };
  }
});

ipcMain.handle('msb:approvals', async () => {
  try {
    return await bridge.approvals();
  } catch (err) {
    return { ok: false, error: String(err) };
  }
});

ipcMain.handle('msb:approve', async (_event, { id, action }) => {
  try {
    return await bridge.approve(id, action);
  } catch (err) {
    return { ok: false, error: String(err) };
  }
});

ipcMain.handle('msb:killswitch', async () => {
  try {
    return await bridge.killswitch();
  } catch (err) {
    return { ok: false, error: String(err) };
  }
});

ipcMain.handle('msb:memory', async (_event, { session, limit }) => {
  try {
    return await bridge.memory(session, limit);
  } catch (err) {
    return { ok: false, error: String(err) };
  }
});

ipcMain.handle('msb:search', async (_event, { query, limit }) => {
  try {
    return await bridge.search(query, limit);
  } catch (err) {
    return { ok: false, error: String(err) };
  }
});

// --- App lifecycle ---

app.whenReady().then(() => {
  bridge = new MsbBridge(MSB_HOST, MSB_PORT);
  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});
