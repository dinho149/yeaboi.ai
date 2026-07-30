/**
 * The live-update loop: back-to-back long-polls of `/api/state`.
 *
 * Each request carries the ETag we hold. If we are already behind, the server
 * answers at once; if we are current, it parks the request on the board's
 * EventHub and answers the instant something changes, or 304s at the deadline.
 * Either way we immediately issue the next one, so the board is effectively
 * push-driven while an idle one costs ~200 bytes per 25 s instead of the ~40 KB
 * per 1.2 s the old fixed poll spent.
 *
 * **Not Server-Sent Events.** SSE was the original design and it does not work
 * here: a Cloudflare quick tunnel withholds a response body until the origin
 * finishes it, so a stream that never ends delivers nothing to the remote
 * teammate — measured against a live tunnel, see `sharing/live.py` for the
 * table. Long-polling is immune because every response is complete.
 *
 * ## Why cleanup aborts
 *
 * Two mechanisms, for two different failures.
 *
 * The `AbortController` cancels the in-flight request, so a teardown does not
 * leave a parked request holding one of the server's four per-IP slots for up
 * to 25 s after nobody is listening. Teardowns are routine: unmounting the
 * board, and switching token — following a link to a different board.
 *
 * The generation counter covers the gap the abort cannot: an `await` that had
 * already resolved must not write into a store the *next* loop now owns, which
 * would apply a snapshot from the previous board.
 *
 * Note that React's StrictMode double-invoke — the usual reason cited for this
 * shape — does not apply while we run on preact: `preact/compat` exports
 * `StrictMode` as `Fragment`, so it is a no-op. The abort is still required for
 * the real teardowns above, and would become required for that reason too if
 * the alias in vite.config.ts is ever flipped back to React.
 */

import { useEffect, useRef, useState } from 'react';

import { pollState, type Session } from '../runtime/api';
import type { BoardStore, Revisioned } from '../store/boardStore';

/** How long the server may park one request. Must stay under its own clamp. */
const WAIT_SECONDS = 25;

/** Backoff after a failed request, in ms. Capped so recovery stays quick. */
const RETRY_MIN_MS = 500;
const RETRY_MAX_MS = 8000;

export type StreamStatus = 'idle' | 'live' | 'retrying';

export interface BoardStreamOptions<S extends Revisioned> {
  session: Session;
  store: BoardStore<S>;
  /** Set false to hold the loop (no token yet, or the tab is hidden). */
  enabled?: boolean;
  waitSeconds?: number;
  path?: string;
}

/**
 * Run the long-poll loop for as long as the component is mounted.
 *
 * Returns a coarse status for the connection pip in the toolbar. `'retrying'`
 * is worth showing: over a tunnel on a phone the difference between "the board
 * is quiet" and "you fell off the network" is otherwise invisible, and people
 * assume the former and keep talking to an empty room.
 */
export function useBoardStream<S extends Revisioned>({
  session,
  store,
  enabled = true,
  waitSeconds = WAIT_SECONDS,
  path = '/api/state',
}: BoardStreamOptions<S>): StreamStatus {
  const [status, setStatus] = useState<StreamStatus>('idle');
  const generation = useRef(0);

  useEffect(() => {
    if (!enabled || !session.token) {
      setStatus('idle');
      return;
    }

    const mine = ++generation.current;
    const controller = new AbortController();
    let etag = '';
    let failures = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const live = (): boolean => generation.current === mine && !controller.signal.aborted;

    async function loop(): Promise<void> {
      while (live()) {
        const result = await pollState<S>(session, {
          etag,
          // The very first request must not park: we hold no ETag, so the
          // server would answer immediately anyway, and asking to wait only
          // burns a hold slot for nothing.
          waitSeconds: etag ? waitSeconds : 0,
          signal: controller.signal,
          path,
        });
        if (!live()) return;

        if ('error' in result) {
          failures += 1;
          setStatus('retrying');
          // Exponential backoff with a ceiling. A dropped tunnel comes back
          // within seconds; hammering it while it is down helps nobody.
          const delay = Math.min(RETRY_MIN_MS * 2 ** (failures - 1), RETRY_MAX_MS);
          await new Promise<void>((resolve) => {
            timer = setTimeout(resolve, delay);
          });
          continue;
        }

        failures = 0;
        etag = result.etag;
        setStatus('live');
        if (result.changed) store.apply(result.data);
      }
    }

    void loop();

    return () => {
      generation.current += 1;
      controller.abort();
      if (timer !== undefined) clearTimeout(timer);
    };
    // session.token identifies the board; a new token means a different board
    // and a loop that must start over with no ETag.
  }, [session, session.token, store, enabled, waitSeconds, path]);

  return status;
}
