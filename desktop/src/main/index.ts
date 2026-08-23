// Electron main — app lifecycle, the one window (boards get their own in M7),
// and the security posture every window shares: contextIsolation, no
// nodeIntegration, sandbox, no navigation off the app, external links to the
// OS browser.

import { join } from 'node:path';
import { BrowserWindow, app, ipcMain, shell } from 'electron';
import { registerApiProxy } from './api-proxy';
import { Sidecar } from './sidecar';

const sidecar = new Sidecar();
let mainWindow: BrowserWindow | null = null;

function createMainWindow(): void {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 840,
    minWidth: 960,
    minHeight: 640,
    show: false,
    backgroundColor: '#0e1013', // --bg midnight — no white flash before first paint
    webPreferences: {
      preload: join(import.meta.dirname, '../preload/index.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  mainWindow.once('ready-to-show', () => mainWindow?.show());
  mainWindow.on('closed', () => {
    mainWindow = null;
  });

  // External links open in the OS browser; anything else is denied.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('https://') || url.startsWith('http://')) void shell.openExternal(url);
    return { action: 'deny' };
  });

  if (process.env['ELECTRON_RENDERER_URL']) {
    void mainWindow.loadURL(process.env['ELECTRON_RENDERER_URL']);
  } else {
    void mainWindow.loadFile(join(import.meta.dirname, '../renderer/index.html'));
  }
}

// Global hardening for every webContents this app ever creates (boards later).
app.on('web-contents-created', (_event, contents) => {
  contents.on('will-navigate', (event, url) => {
    const allowed =
      url.startsWith('http://127.0.0.1') || (process.env['ELECTRON_RENDERER_URL'] ?? '') === url.split('#')[0];
    if (!allowed) event.preventDefault();
  });
  contents.on('will-attach-webview', (event) => event.preventDefault());
});

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });

  void app.whenReady().then(() => {
    registerApiProxy(sidecar);
    // The pull half (a window that mounted after 'ready' would otherwise wait
    // forever for a transition that already happened) and the push half.
    ipcMain.handle('backend:get-state', () => {
      // The renderer never sees the token — strip it before crossing the bridge.
      const state = sidecar.current;
      if (state.kind !== 'ready') return state;
      return { kind: 'ready' };
    });
    sidecar.onState((state) => {
      console.log(`[backend] ${state.kind}${state.kind === 'down' ? `: ${state.reason}` : ''}`);
      // Same stripping as the pull half: the handshake (token) stays in main.
      const safe = state.kind === 'ready' ? { kind: 'ready' } : state;
      for (const window of BrowserWindow.getAllWindows()) {
        window.webContents.send('backend:state', safe);
      }
    });
    void sidecar.start();
    createMainWindow();

    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) createMainWindow();
    });
  });

  app.on('window-all-closed', () => {
    // Tray persistence (pet + awareness) lands in M10; until then closing the
    // last window quits — including on macOS, where a dockless zombie with no
    // windows and no tray would be unreachable.
    app.quit();
  });

  let cleanShutdown = false;
  app.on('before-quit', (event) => {
    if (cleanShutdown) return;
    event.preventDefault();
    void sidecar.stop().finally(() => {
      cleanShutdown = true;
      app.quit();
    });
  });
}
