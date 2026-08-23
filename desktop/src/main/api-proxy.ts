// The renderer's only path to the backend. The bearer token never leaves the
// main process: the renderer calls window.yeaboi.api(path, init) → preload →
// ipcMain here → fetch with the Authorization header attached.

import { ipcMain } from 'electron';
import type { Sidecar } from './sidecar';

export interface ApiResult {
  status: number;
  body: unknown;
}

interface ApiInit {
  method?: string;
  body?: unknown;
}

const ALLOWED_METHODS = new Set(['GET', 'POST']);

export function registerApiProxy(sidecar: Sidecar): void {
  ipcMain.handle('api:request', async (_event, path: unknown, init: unknown): Promise<ApiResult> => {
    if (typeof path !== 'string' || !path.startsWith('/api/')) {
      return { status: 400, body: { error: 'path must start with /api/' } };
    }
    const handshake = sidecar.handshake;
    if (!handshake) {
      return { status: 503, body: { error: 'backend is not running' } };
    }
    const options = (init ?? {}) as ApiInit;
    const method = (options.method ?? 'GET').toUpperCase();
    if (!ALLOWED_METHODS.has(method)) {
      return { status: 400, body: { error: `method not allowed over the proxy: ${method}` } };
    }
    try {
      const response = await fetch(`${handshake.url}${path}`, {
        method,
        headers: {
          Authorization: `Bearer ${handshake.token}`,
          ...(options.body !== undefined ? { 'Content-Type': 'application/json' } : {}),
        },
        ...(options.body !== undefined ? { body: JSON.stringify(options.body) } : {}),
      });
      return { status: response.status, body: await response.json() };
    } catch (error) {
      return { status: 502, body: { error: `backend request failed: ${(error as Error).message}` } };
    }
  });
}
