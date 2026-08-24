// Electron main — app lifecycle, the main window, and the security posture
// every window shares: contextIsolation, no nodeIntegration, sandbox, no
// navigation off the app, external links to the OS browser. Live boards get
// their own top-level windows (boards.ts) because a board page refuses to be
// framed and its host URL carries an admin token.
//
// Since M10 the app also persists in the tray with the window closed: the duck
// pet is a window of its own, and awareness is only worth anything if the app
// is still there to notice.

import { join } from 'node:path';
import { BrowserWindow, app, ipcMain, session, shell } from 'electron';
import { callApi, registerApiProxy } from './api-proxy';
import { closeAllBoardWindows, registerBoardWindows } from './boards';
import { EventReader, broadcast } from './events';
import { Pet, type PetNotice } from './pet';
import { installPermissionHandlers } from './permissions';
import { Sidecar } from './sidecar';
import { AppTray } from './tray';
import { Updater } from './updater';

const sidecar = new Sidecar();
const pet = new Pet();
const events = new EventReader(sidecar);
const updater = new Updater();
let mainWindow: BrowserWindow | null = null;
let tray: AppTray | null = null;

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

/** Bring the app forward, optionally at a route. Used by the tray and by a
 *  click on a duck that is holding a question. */
function openApp(route = ''): void {
  if (!mainWindow || mainWindow.isDestroyed()) createMainWindow();
  const window = mainWindow;
  if (!window) return;
  if (window.isMinimized()) window.restore();
  window.show();
  window.focus();
  if (route) window.webContents.send('app:navigate', route);
}

/** Bring the window forward with the About panel open. The panel is a modal,
 *  not a route, so the tray asks for it rather than navigating to it. */
function showAbout(): void {
  openApp();
  mainWindow?.webContents.send('app:about');
}

/** The pet's on/off state lives in the backend so the terminal, the tray and
 *  the app's own settings cannot disagree about it. */
async function loadPetPreference(): Promise<boolean> {
  const result = await callApi(sidecar, '/api/ambience');
  if (result.status !== 200) return false;
  return Boolean((result.body as { pet?: { enabled?: boolean } }).pet?.enabled);
}

async function setPetPreference(enabled: boolean): Promise<void> {
  pet.setEnabled(enabled);
  tray?.setPetEnabled(enabled);
  await callApi(sidecar, '/api/ambience', { method: 'POST', body: { pet_enabled: enabled } });
}

// Global hardening for every webContents this app ever creates (boards, pet).
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
  app.on('second-instance', () => openApp());

  void app.whenReady().then(() => {
    installPermissionHandlers(
      (listener) => app.on('session-created', listener),
      session.defaultSession,
      (contents) => contents !== null && contents === mainWindow?.webContents,
    );
    registerApiProxy(sidecar);
    registerBoardWindows(sidecar);
    pet.register((route) => openApp(route));
    // The pull half (a window that mounted after 'ready' would otherwise wait
    // forever for a transition that already happened) and the push half.
    ipcMain.handle('backend:get-state', () => {
      // The renderer never sees the token — strip it before crossing the bridge.
      const state = sidecar.current;
      if (state.kind !== 'ready') return state;
      return { kind: 'ready' };
    });
    ipcMain.handle('pet:set-enabled', async (_event, enabled: unknown) => {
      await setPetPreference(Boolean(enabled));
      return { enabled: pet.on };
    });
    // What the About panel shows about the shell itself; everything else it
    // knows (the yeaboi version, the bundled Python) comes from the backend.
    ipcMain.handle('app:meta', () => ({
      version: app.getVersion(),
      electron: process.versions.electron,
      chrome: process.versions.chrome,
      platform: process.platform,
      arch: process.arch,
      packaged: app.isPackaged,
    }));
    ipcMain.handle('update:get-state', () => updater.current);
    ipcMain.handle('update:check', () => updater.check());
    ipcMain.handle('update:download', () => updater.download());
    ipcMain.handle('update:install', () => updater.install());
    updater.onState((state) => {
      tray?.setUpdateState(state);
      for (const window of BrowserWindow.getAllWindows()) window.webContents.send('update:state', state);
    });
    sidecar.onState((state) => {
      console.log(`[backend] ${state.kind}${state.kind === 'down' ? `: ${state.reason}` : ''}`);
      // Same stripping as the pull half: the handshake (token) stays in main.
      const safe = state.kind === 'ready' ? { kind: 'ready' } : state;
      for (const window of BrowserWindow.getAllWindows()) {
        window.webContents.send('backend:state', safe);
      }
      // The duck stays on screen when the backend falls over — he just stops
      // knowing anything, which is the honest degradation.
      if (state.kind === 'ready') {
        void loadPetPreference().then((enabled) => {
          pet.setEnabled(enabled);
          tray?.setPetEnabled(enabled);
        });
      }
    });

    // One reader for the ambient feed: the renderer gets everything (the
    // consent modal lives there), the duck gets only what he can say.
    events.start();
    broadcast(events, () => (mainWindow && !mainWindow.isDestroyed() ? [mainWindow] : []));
    events.on((event) => {
      if (event.type !== 'notice') return;
      pet.notify({
        quip: String(event['quip'] ?? ''),
        sticky: Boolean(event['sticky']),
        route: String(event['route'] ?? ''),
      } satisfies PetNotice);
    });

    void sidecar.start();
    createMainWindow();
    tray = new AppTray(pet, {
      open: () => openApp(),
      about: () => showAbout(),
      // One menu item for the whole update sequence: it does whatever the
      // state it is showing says it does.
      update: () => {
        if (updater.current.kind === 'ready') updater.install();
        else if (updater.current.kind === 'available') void updater.download();
        else void updater.check();
      },
      togglePet: (enabled) => void setPetPreference(enabled),
      quit: () => app.quit(),
    });
    tray.create(false);

    app.on('activate', () => openApp());
  });

  app.on('window-all-closed', () => {
    // Deliberately no quit: the tray is the app's other home, and the duck may
    // still be on the desktop with the window shut. Quit is the tray's Quit.
  });

  let cleanShutdown = false;
  app.on('before-quit', (event) => {
    if (cleanShutdown) return;
    event.preventDefault();
    // The windows first: the sidecar's own shutdown closes the board servers
    // underneath them, and a window left pointing at a dead port shows an
    // error page on the way out.
    closeAllBoardWindows();
    pet.hide();
    events.stop();
    tray?.destroy();
    void sidecar.stop().finally(() => {
      cleanShutdown = true;
      app.quit();
    });
  });
}
