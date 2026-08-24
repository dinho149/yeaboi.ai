// Board windows — the retro and poker boards, each in its own top-level window.
//
// Two facts force this shape:
//
// * a board page sends `X-Frame-Options: DENY`, so it can never be embedded;
// * the host URL carries the admin token that makes its holder the host, so it
//   never crosses into the renderer. The renderer asks for a board *by id*; the
//   URL is served only by `/api/boards/{id}/host`, which main fetches itself
//   and the proxy refuses to relay (`api-proxy.ts`'s MAIN_ONLY).
//
// A board window is not the app: no preload, sandboxed, its own partition, and
// navigation pinned to the loopback origin it opened on. Everything else — an
// external link a card happens to contain — goes to the OS browser.

import { BrowserWindow, ipcMain, shell } from 'electron';
import type { Sidecar } from './sidecar';

const windows = new Map<string, BrowserWindow>();

const TITLES: Record<string, string> = { retro: 'Retro board', poker: 'Planning poker' };

interface BoardSnapshot {
  board_id: string;
  kind: string;
  title: string;
}

export function registerBoardWindows(sidecar: Sidecar): void {
  ipcMain.handle('boards:open', async (_event, boardId: unknown) => {
    if (typeof boardId !== 'string' || !/^[a-f0-9]{1,32}$/.test(boardId)) {
      return { ok: false, error: 'invalid board id' };
    }
    const existing = windows.get(boardId);
    if (existing && !existing.isDestroyed()) {
      // Focus rather than opening a second window onto the same board: two
      // views of one ceremony is confusing, not useful.
      if (existing.isMinimized()) existing.restore();
      existing.focus();
      return { ok: true, focused: true };
    }
    const handshake = sidecar.handshake;
    if (!handshake) return { ok: false, error: 'backend is not running' };
    let board: BoardSnapshot;
    let hostUrl: string;
    try {
      const get = (suffix: string) =>
        fetch(`${handshake.url}/api/boards/${boardId}${suffix}`, {
          headers: { Authorization: `Bearer ${handshake.token}` },
        });
      const response = await get('');
      if (!response.ok) return { ok: false, error: `no live board ${boardId}` };
      board = (await response.json()) as BoardSnapshot;
      // The host link is a second call because it is a secret: it is served
      // only to main, and never rides in the snapshot the app itself reads.
      const hostResponse = await get('/host');
      if (!hostResponse.ok) return { ok: false, error: `no host link for board ${boardId}` };
      hostUrl = ((await hostResponse.json()) as { host_url: string }).host_url;
    } catch (error) {
      return { ok: false, error: `could not read the board: ${(error as Error).message}` };
    }
    openWindow(boardId, board, hostUrl);
    return { ok: true };
  });
}

function openWindow(boardId: string, board: BoardSnapshot, hostUrl: string): void {
  const window = new BrowserWindow({
    width: 1180,
    height: 800,
    minWidth: 720,
    minHeight: 520,
    title: `${TITLES[board.kind] ?? 'Board'} — ${board.title}`,
    backgroundColor: '#0e1013',
    webPreferences: {
      // No preload at all: a board page is the same document a teammate opens
      // in a browser, and it must not gain a bridge here that it lacks there.
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      partition: `board:${boardId}`,
    },
  });
  windows.set(boardId, window);
  window.on('closed', () => windows.delete(boardId));

  const origin = new URL(hostUrl).origin;
  window.webContents.on('will-navigate', (event, url) => {
    if (!url.startsWith(origin)) event.preventDefault();
  });
  window.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('https://') || url.startsWith('http://')) void shell.openExternal(url);
    return { action: 'deny' };
  });
  void window.loadURL(hostUrl);
}

/** Close one board's window, if it has one. Called when the board ends. */
export function closeBoardWindow(boardId: string): void {
  const window = windows.get(boardId);
  if (window && !window.isDestroyed()) window.close();
}

/** Close every board window — the app is quitting. */
export function closeAllBoardWindows(): void {
  for (const window of windows.values()) {
    if (!window.isDestroyed()) window.close();
  }
  windows.clear();
}
