// The renderer's entire capability surface, typed and narrow. Nothing here
// exposes Node, the backend URL, or the bearer token — api() is a blind relay
// into the main process.

import { contextBridge, ipcRenderer } from 'electron';

export interface YeaboiBridge {
  api: (path: string, init?: { method?: string; body?: unknown }) => Promise<{ status: number; body: unknown }>;
  apiStream: (
    path: string,
    body: unknown,
    onLine: (line: unknown) => void,
  ) => Promise<{ status: number; body: unknown }>;
  getBackendState: () => Promise<unknown>;
  onBackendState: (callback: (state: unknown) => void) => void;
  /** The ambient feed, read once in main and pushed here: consent requests and
   *  the awareness notices. */
  onEvent: (callback: (event: unknown) => void) => void;
  /** Main asking the app to show a route — the tray, or a click on the duck. */
  onNavigate: (callback: (route: string) => void) => void;
  /** The desktop pet's on/off, persisted through the backend. */
  setPetEnabled: (enabled: boolean) => Promise<unknown>;
  /** Open one live board in its own top-level window, by id. */
  openBoard: (boardId: string) => Promise<unknown>;
  /** The shell's own identity — versions the backend cannot know. */
  appMeta: () => Promise<unknown>;
  /** Self-update: state, then the three steps a person drives. */
  onUpdateState: (callback: (state: unknown) => void) => void;
  getUpdateState: () => Promise<unknown>;
  checkForUpdate: () => Promise<unknown>;
  downloadUpdate: () => Promise<unknown>;
  installUpdate: () => Promise<unknown>;
  /** The tray asking for the About panel, which is a modal and not a route. */
  onAbout: (callback: () => void) => void;
  platform: string;
}

const bridge: YeaboiBridge = {
  api: (path, init) => ipcRenderer.invoke('api:request', path, init),
  apiStream: (path, body, onLine) => {
    // The channel is per call, so two concurrent turns never cross lines; the
    // listener is removed when the stream ends, however it ends.
    const id = `${Date.now().toString(36)}-${Math.floor(Math.random() * 1e9).toString(36)}`;
    const channel = `api:stream:${id}`;
    const handler = (_event: unknown, line: unknown) => onLine(line);
    ipcRenderer.on(channel, handler);
    return ipcRenderer
      .invoke('api:stream', path, { method: 'POST', body }, id)
      .finally(() => ipcRenderer.removeListener(channel, handler));
  },
  getBackendState: () => ipcRenderer.invoke('backend:get-state'),
  onEvent: (callback) => {
    ipcRenderer.on('app:event', (_event, payload: unknown) => callback(payload));
  },
  onNavigate: (callback) => {
    ipcRenderer.on('app:navigate', (_event, route: string) => callback(route));
  },
  setPetEnabled: (enabled) => ipcRenderer.invoke('pet:set-enabled', enabled),
  openBoard: (boardId) => ipcRenderer.invoke('boards:open', boardId),
  onBackendState: (callback) => {
    ipcRenderer.on('backend:state', (_event, state: unknown) => callback(state));
  },
  appMeta: () => ipcRenderer.invoke('app:meta'),
  onUpdateState: (callback) => {
    ipcRenderer.on('update:state', (_event, state: unknown) => callback(state));
  },
  getUpdateState: () => ipcRenderer.invoke('update:get-state'),
  checkForUpdate: () => ipcRenderer.invoke('update:check'),
  downloadUpdate: () => ipcRenderer.invoke('update:download'),
  installUpdate: () => ipcRenderer.invoke('update:install'),
  onAbout: (callback) => {
    ipcRenderer.on('app:about', () => callback());
  },
  platform: process.platform,
};

contextBridge.exposeInMainWorld('yeaboi', bridge);
