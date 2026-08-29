/**
 * Headless smoke: boot the real desktop main process, let the renderer
 * attach to the live msb-v3, capture a PNG of the rendered cockpit, exit.
 *
 *   MSB_DESKTOP_SMOKE=/abs/out.png ./node_modules/.bin/electron scripts/smoke.js
 *
 * Dev affordance only - kept out of src/ so the shell's "no fs, no capture"
 * invariants stay clean. Reuses src/main/index.js verbatim (same window,
 * same hardening, same IPC, same bridge).
 */

'use strict';

const fs = require('fs');
const { app } = require('electron');
const main = require('../src/main/index.js');

const OUT = process.env.MSB_DESKTOP_SMOKE || '/tmp/msb-desktop-smoke.png';
const SETTLE_MS = Number(process.env.MSB_DESKTOP_SMOKE_WAIT || 3000);

main.setOnWindow((win) => {
  win.webContents.once('did-finish-load', () => {
    setTimeout(async () => {
      try {
        const img = await win.webContents.capturePage();
        fs.writeFileSync(OUT, img.toPNG());
        const runtimeState = await win.webContents
          .executeJavaScript('window.__runtimeState || "unknown"')
          .catch(() => 'unknown');
        console.log('SMOKE_OK', OUT, 'runtimeState=' + runtimeState);
        app.exit(runtimeState === 'READY' ? 0 : 2);
      } catch (err) {
        console.error('SMOKE_FAIL', String((err && err.message) || err));
        app.exit(1);
      }
    }, SETTLE_MS);
  });
});
