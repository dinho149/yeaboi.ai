// The duck pet: one transparent, click-through, always-on-top window stretched
// across the primary display.
//
// The trick that makes a desktop pet work at all is `setIgnoreMouseEvents(true,
// {forward: true})` — every app underneath keeps working normally, but the
// renderer still hears about the cursor. When the pointer is over the duck's
// hitbox the renderer says so (`pet:interactive`) and the window goes solid for
// that instant, which is the only reason the duck is grab-able.
//
// Ported from the standalone desktop-pet prototype (origin/feature/desktop-pet).
// The physics, gait and sprites are unchanged; what is new is that the window
// belongs to a real app rather than being one — no `app.dock.hide()`, a shared
// tray, and a duck that knows what yeaboi is doing.

import { join } from 'node:path';
import { execFile } from 'node:child_process';
import { BrowserWindow, ipcMain, screen } from 'electron';
import { DOCK_SCRIPT, type DockRect, dockConfig, parseDockRect } from './dock';

/** Window-local cursor feed rate. 16ms is one frame at 60fps — the duck flees
 *  a moving pointer, so a slower feed reads as a stutter. */
const CURSOR_FEED_MS = 16;
/** The dock can move, resize or hide; the floor is re-polled on this cadence. */
const LAYOUT_POLL_MS = 2_000;

export interface PetNotice {
  quip: string;
  sticky: boolean;
  route: string;
}

/** Ask macOS where the Dock is, so the duck can stand on it and walk off its
 *  edges. Uses the Accessibility API via System Events — the first call prompts
 *  for permission, and a refusal simply means floor-only. */
function queryDockRect(callback: (rect: DockRect | null) => void): void {
  if (process.platform !== 'darwin') {
    callback(null);
    return;
  }
  const script = DOCK_SCRIPT.flatMap((line) => ['-e', line]);
  execFile('osascript', script, { timeout: 1_500 }, (error, stdout) => {
    callback(error ? null : parseDockRect(stdout));
  });
}

export class Pet {
  private window: BrowserWindow | null = null;
  private timers: NodeJS.Timeout[] = [];
  private enabled = false;
  /** Where a click on a duck holding a question should land. */
  private onOpen: (route: string) => void = () => undefined;

  get on(): boolean {
    return this.enabled;
  }

  /** Wire the IPC the pet renderer talks over. Called once, at app ready. */
  register(onOpen: (route: string) => void): void {
    this.onOpen = onOpen;
    // The renderer decides, frame by frame, whether the pointer is over the
    // duck; `over` makes the window solid so the click lands on him.
    ipcMain.on('pet:interactive', (_event, over: unknown) => {
      const window = this.window;
      if (window && !window.isDestroyed()) window.setIgnoreMouseEvents(!over, { forward: true });
    });
    ipcMain.on('pet:open', (_event, route: unknown) => {
      this.onOpen(typeof route === 'string' ? route : '');
    });
  }

  setEnabled(enabled: boolean): void {
    this.enabled = enabled;
    if (enabled) this.show();
    else this.hide();
  }

  /** Tell the duck about something that happened while nobody was looking. */
  notify(notice: PetNotice): void {
    const window = this.window;
    if (window && !window.isDestroyed()) window.webContents.send('pet:notice', notice);
  }

  nudge(delta: number): void {
    this.window?.webContents.send('pet:nudge', delta);
  }

  recenter(): void {
    this.window?.webContents.send('pet:recenter');
  }

  hide(): void {
    for (const timer of this.timers) clearInterval(timer);
    this.timers = [];
    if (this.window && !this.window.isDestroyed()) this.window.destroy();
    this.window = null;
  }

  private show(): void {
    if (this.window && !this.window.isDestroyed()) return;
    const display = screen.getPrimaryDisplay();
    const { x, y, width, height } = display.bounds; // full bounds, so the dock is covered
    // The floor is the gap between the display bottom and the usable work area:
    // the macOS dock, the Windows taskbar, a Linux panel. The duck stands on it.
    const workArea = display.workArea;
    const bottomInset = Math.max(0, y + height - (workArea.y + workArea.height));

    const window = new BrowserWindow({
      x,
      y,
      width,
      height,
      frame: false,
      transparent: true,
      hasShadow: false,
      resizable: false,
      movable: false,
      minimizable: false,
      maximizable: false,
      fullscreenable: false,
      skipTaskbar: true,
      focusable: false, // never steal focus from whatever is being worked in
      alwaysOnTop: true,
      webPreferences: {
        preload: join(import.meta.dirname, '../preload/pet.cjs'),
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
      },
    });
    this.window = window;

    // 'screen-saver' is the highest normal level — above the dock and above
    // other always-on-top windows.
    window.setAlwaysOnTop(true, 'screen-saver');
    window.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
    window.setIgnoreMouseEvents(true, { forward: true });

    const rendererUrl = process.env['ELECTRON_RENDERER_URL'];
    if (rendererUrl) void window.loadURL(`${rendererUrl}/pet/index.html`);
    else void window.loadFile(join(import.meta.dirname, '../renderer/pet/index.html'));

    const sendLayout = (): void => {
      if (!this.window || this.window.isDestroyed()) return;
      queryDockRect((rect) => {
        if (!this.window || this.window.isDestroyed()) return;
        this.window.webContents.send('pet:config', { bottomInset, dock: dockConfig(rect, { x, y }) });
      });
    };
    window.webContents.once('did-finish-load', sendLayout);
    this.timers.push(setInterval(sendLayout, LAYOUT_POLL_MS));

    // The cursor is polled rather than taken from forwarded DOM events: those
    // only fire while the pointer is over the window, which a click-through
    // window cannot rely on.
    this.timers.push(
      setInterval(() => {
        if (!this.window || this.window.isDestroyed()) return;
        const point = screen.getCursorScreenPoint();
        this.window.webContents.send('pet:cursor', { x: point.x - x, y: point.y - y });
      }, CURSOR_FEED_MS),
    );

    window.on('closed', () => {
      for (const timer of this.timers) clearInterval(timer);
      this.timers = [];
      this.window = null;
    });
  }
}
