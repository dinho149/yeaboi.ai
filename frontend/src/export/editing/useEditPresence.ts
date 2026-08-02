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
  //
  // `actions` is in here for the same reason and it is the one that mattered:
  // `EditApp` re-memoises it whenever the identity or the revision changes, so
  // depending on it tore the interval down and fired an immediate heartbeat on
  // every keystroke of the name and every edit anyone made — which is exactly
  // what the ref above exists to prevent, undone by the dependency array.
  const latest = useRef({ identity, editingPath, actions });
  latest.current = { identity, editingPath, actions };

  useEffect(() => {
    if (!enabled) return;
    let stopped = false;
    function beat() {
      if (stopped) return;
      const { identity: who, editingPath: path, actions: send } = latest.current;
      void send.presence(who.name, who.avatar, path);
    }
    beat();
    const timer = setInterval(beat, INTERVAL_MS);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [enabled]);
}
