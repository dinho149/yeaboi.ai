// The ambient feed, read once in main and fanned out from there.
//
// GET /api/events is a single long-lived SSE stream carrying everything the
// backend needs to *tell* the shell rather than answer it: sandbox consent
// requests, and the awareness notices for things that happened while nobody was
// looking. One reader, not one per window — the token lives here, and a second
// subscriber would double every event.
//
// The stream dies whenever the backend does. Rather than reconnect on a timer,
// the reader follows the sidecar's own state: it opens on `ready` and closes on
// anything else, so a backoff restart brings the feed back with it.

import type { BrowserWindow } from 'electron';
import type { Sidecar } from './sidecar';

export interface AmbientEvent {
  type: string;
  seq?: number;
  [key: string]: unknown;
}

type Listener = (event: AmbientEvent) => void;

/**
 * Split whatever has arrived into whole SSE frames, keeping the remainder.
 *
 * Frames are separated by a blank line and only `data:` lines carry a payload,
 * so the feed's `: ping` comments fall out here rather than being special-cased.
 * A frame that will not decode is dropped with a line on the console: one bad
 * event must not end a stream that has been open for hours.
 */
export function parseFrames(buffer: string): { events: AmbientEvent[]; rest: string } {
  const events: AmbientEvent[] = [];
  let rest = buffer;
  let cut = rest.indexOf('\n\n');
  while (cut >= 0) {
    for (const line of rest.slice(0, cut).split('\n')) {
      if (!line.startsWith('data:')) continue;
      try {
        events.push(JSON.parse(line.slice(5).trim()) as AmbientEvent);
      } catch {
        console.error('[events] undecodable frame');
      }
    }
    rest = rest.slice(cut + 2);
    cut = rest.indexOf('\n\n');
  }
  return { events, rest };
}

export class EventReader {
  private controller: AbortController | null = null;
  private listeners = new Set<Listener>();

  constructor(private readonly sidecar: Sidecar) {}

  /** Follow the backend: open the feed while it is up, close it when it is not. */
  start(): void {
    this.sidecar.onState((state) => {
      this.stop();
      if (state.kind === 'ready') void this.read();
    });
  }

  stop(): void {
    this.controller?.abort();
    this.controller = null;
  }

  on(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private emit(event: AmbientEvent): void {
    for (const listener of this.listeners) listener(event);
  }

  private async read(): Promise<void> {
    const handshake = this.sidecar.handshake;
    if (!handshake) return;
    const controller = new AbortController();
    this.controller = controller;
    try {
      const response = await fetch(`${handshake.url}/api/events`, {
        headers: { Authorization: `Bearer ${handshake.token}` },
        signal: controller.signal,
      });
      if (!response.ok || !response.body) {
        console.error(`[events] feed refused: ${response.status}`);
        return;
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        const { events, rest } = parseFrames(buffer + decoder.decode(value, { stream: true }));
        buffer = rest;
        for (const event of events) this.emit(event);
      }
    } catch (error) {
      if (!controller.signal.aborted) console.error(`[events] feed dropped: ${(error as Error).message}`);
    }
  }
}

/** Push every ambient event at every open window, for the renderer's own use. */
export function broadcast(reader: EventReader, windows: () => BrowserWindow[]): void {
  reader.on((event) => {
    for (const window of windows()) {
      if (!window.webContents.isDestroyed()) window.webContents.send('app:event', event);
    }
  });
}
