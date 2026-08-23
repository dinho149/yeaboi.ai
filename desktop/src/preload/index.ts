// The renderer's entire capability surface, typed and narrow. Nothing here
// exposes Node, the backend URL, or the bearer token — api() is a blind relay
// into the main process.

import { contextBridge, ipcRenderer } from 'electron';

export interface YeaboiBridge {
  api: (path: string, init?: { method?: string; body?: unknown }) => Promise<{ status: number; body: unknown }>;
  getBackendState: () => Promise<unknown>;
  onBackendState: (callback: (state: unknown) => void) => void;
  platform: string;
}

const bridge: YeaboiBridge = {
  api: (path, init) => ipcRenderer.invoke('api:request', path, init),
  getBackendState: () => ipcRenderer.invoke('backend:get-state'),
  onBackendState: (callback) => {
    ipcRenderer.on('backend:state', (_event, state: unknown) => callback(state));
  },
  platform: process.platform,
};

contextBridge.exposeInMainWorld('yeaboi', bridge);
