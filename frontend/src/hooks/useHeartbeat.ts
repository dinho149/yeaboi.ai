/**
 * Presence heartbeat — "I am still here, and this is what I am typing".
 *
 * Separate from {@link useBoardStream} because it is the opposite direction:
 * the stream is how state reaches this browser, the heartbeat is how this
 * browser's existence reaches everyone else. The server expires a participant
 * from the who's-here row after a few missed beats.
 *
 * It posts with `?quiet=1`, which answers `{"ok":true}` instead of a full
 * snapshot. The beat still has to be sent about once a second; what changed
 * with long-polling is that its *response* no longer needs to carry ~40 KB of
 * board state, because the stream already delivers that the moment it changes.
 *
 * The typing indicator rides along rather than getting its own endpoint: it is
 * the same "here is what I am doing right now" fact, it expires on the same
 * schedule, and a separate endpoint would double the request rate for it.
 */

import { useEffect, useRef } from 'react';

import { postJSON, type Session } from '../runtime/api';

const BEAT_MS = 1000;

export interface HeartbeatOptions {
  session: Session;
  /** Display name and avatar as the rest of the room should see them. */
  name: string;
  avatar: string;
  /** Grid the user is currently typing into, or `''`. */
  typingGrid?: string;
  /** False until the user has actually joined (named themselves). */
  enabled?: boolean;
  intervalMs?: number;
  path?: string;
}

export function useHeartbeat({
  session,
  name,
  avatar,
  typingGrid = '',
  enabled = true,
  intervalMs = BEAT_MS,
  path = '/api/presence?quiet=1',
}: HeartbeatOptions): void {
  // The beat fires on a timer but must send the *current* name/avatar/typing
  // state. Holding them in a ref means changing any of them does not tear down
  // and restart the interval — which, at a one-second period, would otherwise
  // mean a keystroke could delay or duplicate a beat.
  const latest = useRef({ name, avatar, typingGrid });
  latest.current = { name, avatar, typingGrid };

  useEffect(() => {
    if (!enabled || !session.token) return;

    let stopped = false;
    const controller = new AbortController();

    async function beat(): Promise<void> {
      if (stopped) return;
      await postJSON(
        session,
        path,
        {
          name: latest.current.name,
          avatar: latest.current.avatar,
          typing_grid: latest.current.typingGrid,
        },
        { signal: controller.signal }
      );
      // Failures are deliberately ignored: a missed beat costs at most a brief
      // absence from the presence row, and the next one repairs it. Surfacing
      // it would put an error in front of the user once per dropped packet.
    }

    void beat(); // announce immediately rather than after the first interval
    const timer = setInterval(() => void beat(), intervalMs);

    return () => {
      stopped = true;
      controller.abort();
      clearInterval(timer);
    };
  }, [session, session.token, enabled, intervalMs, path]);
}
