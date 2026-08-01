/**
 * Tell the document who is here, and what they have open.
 *
 * Not `hooks/useHeartbeat`: that hook spells its wire keys inside itself rather
 * than in an `actions.ts`, which makes them invisible to
 * `test_web_request_keys.py`. Routing presence through `export/actions.ts`
 * instead is what lets the new guard row actually cover every key this page
 * sends. (The boards' own heartbeat has the same gap; it predates this.)
 */

import { useEffect, useRef } from 'react';

import type { EditActions } from '../actions';

/** Matches the server's PRESENCE_TTL of 12s with room for a dropped beat. */
const INTERVAL_MS = 4000;

export function useEditPresence(
  actions: EditActions,
  identity: { name: string; avatar: string },
  editingPath: string,
  enabled: boolean
): void {
  // Held in a ref so changing your name does not restart the interval — the
  // next beat simply carries the new value.
  const latest = useRef({ identity, editingPath });
  latest.current = { identity, editingPath };

  useEffect(() => {
    if (!enabled) return;
    let stopped = false;
    function beat() {
      if (stopped) return;
      const { identity: who, editingPath: path } = latest.current;
      void actions.presence(who.name, who.avatar, path);
    }
    beat();
    const timer = setInterval(beat, INTERVAL_MS);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [actions, enabled]);
}
