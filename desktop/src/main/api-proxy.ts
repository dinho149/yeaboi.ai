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

/** One authed call to the backend. The only place the bearer token is used.
 *
 * Exported because main itself is a client: the tray reads and writes the
 * ambience preferences, and the event reader opens the SSE feed. */
export async function callApi(sidecar: Sidecar, path: string, init: ApiInit = {}): Promise<ApiResult> {
  const handshake = sidecar.handshake;
  if (!handshake) return { status: 503, body: { error: 'backend is not running' } };
  const method = (init.method ?? 'GET').toUpperCase();
  try {
    const response = await fetch(`${handshake.url}${path}`, {
      method,
      headers: {
        Authorization: `Bearer ${handshake.token}`,
        ...(init.body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      },
      ...(init.body !== undefined ? { body: JSON.stringify(init.body) } : {}),
    });
    return { status: response.status, body: await response.json() };
  } catch (error) {
    return { status: 502, body: { error: `backend request failed: ${(error as Error).message}` } };
  }
}

// A stream id names an IPC channel, so it is checked rather than trusted: an
// arbitrary string here would let the renderer pick which channel main sends on.
const STREAM_ID = /^[a-z0-9-]{1,64}$/i;

export function registerApiProxy(sidecar: Sidecar): void {
  ipcMain.handle('api:request', async (_event, path: unknown, init: unknown): Promise<ApiResult> => {
    if (typeof path !== 'string' || !path.startsWith('/api/')) {
      return { status: 400, body: { error: 'path must start with /api/' } };
    }
    const options = (init ?? {}) as ApiInit;
    const method = (options.method ?? 'GET').toUpperCase();
    if (!ALLOWED_METHODS.has(method)) {
      return { status: 400, body: { error: `method not allowed over the proxy: ${method}` } };
    }
    return callApi(sidecar, path, options);
  });

  // The NDJSON half: a chat turn arrives a line at a time, so the response is
  // read as it streams and each parsed line is pushed to the calling window.
  // Resolves once the stream ends; the renderer awaits that as "turn over".
  ipcMain.handle('api:stream', async (event, path: unknown, init: unknown, streamId: unknown): Promise<ApiResult> => {
    if (typeof path !== 'string' || !path.startsWith('/api/')) {
      return { status: 400, body: { error: 'path must start with /api/' } };
    }
    if (typeof streamId !== 'string' || !STREAM_ID.test(streamId)) {
      return { status: 400, body: { error: 'invalid stream id' } };
    }
    const handshake = sidecar.handshake;
    if (!handshake) return { status: 503, body: { error: 'backend is not running' } };
    const options = (init ?? {}) as ApiInit;
    const channel = `api:stream:${streamId}`;
    try {
      const response = await fetch(`${handshake.url}${path}`, {
        method: (options.method ?? 'POST').toUpperCase(),
        headers: { Authorization: `Bearer ${handshake.token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify(options.body ?? {}),
      });
      if (!response.ok || !response.body) {
        return { status: response.status, body: await response.json().catch(() => ({ error: 'stream failed' })) };
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let cut = buffer.indexOf('\n');
        while (cut >= 0) {
          const line = buffer.slice(0, cut).trim();
          buffer = buffer.slice(cut + 1);
          if (line && !event.sender.isDestroyed()) event.sender.send(channel, JSON.parse(line));
          cut = buffer.indexOf('\n');
        }
      }
      return { status: response.status, body: {} };
    } catch (error) {
      return { status: 502, body: { error: `stream failed: ${(error as Error).message}` } };
    }
  });
}
