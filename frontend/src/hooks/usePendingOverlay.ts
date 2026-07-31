/**
 * Optimistic UI for a single server-owned value.
 *
 * The problem: you tap a vote and nothing happens for a round trip plus up to a
 * change-watcher tick. On a phone over a tunnel that is long enough to tap
 * again, so people double-vote and then stop trusting the board.
 *
 * The contract:
 *
 * * `set(value)` shows your value immediately.
 * * The next server value resolves the wait, whatever it says. If it agrees,
 *   the handover is invisible — same value either side. If it *disagrees*, the
 *   server wins at once: it is the authority, and the usual cause is a host
 *   action (a revote, a lock) that should visibly override you.
 * * If nothing arrives within `timeoutMs`, the overlay reverts and `onTimeout`
 *   fires. Silent divergence is the failure to avoid — an overlay that never
 *   clears shows you a vote nobody else can see.
 *
 * Used for votes, carried-item status, the lock toggle, and theme cast.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

const TIMEOUT_MS = 5000;

export interface PendingOverlay<T> {
  /** What to render: the pending value if there is one, else the server's. */
  value: T;
  /** True while waiting for the server to confirm. */
  pending: boolean;
  /** Show a value optimistically. */
  set(next: T): void;
  /** Abandon the optimistic value and fall back to the server's. */
  clear(): void;
}

export function usePendingOverlay<T>(
  serverValue: T,
  {
    timeoutMs = TIMEOUT_MS,
    isEqual = Object.is as (a: T, b: T) => boolean,
    onTimeout,
  }: {
    timeoutMs?: number;
    isEqual?: (a: T, b: T) => boolean;
    onTimeout?: () => void;
  } = {}
): PendingOverlay<T> {
  const [pending, setPending] = useState<{ value: T } | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  // Held in refs so a caller passing inline arrow functions — the normal way to
  // write these — does not change the identity of `set` on every render.
  const onTimeoutRef = useRef(onTimeout);
  const isEqualRef = useRef(isEqual);
  const serverRef = useRef(serverValue);
  onTimeoutRef.current = onTimeout;
  isEqualRef.current = isEqual;
  serverRef.current = serverValue;

  const stopTimer = useCallback(() => {
    if (timer.current !== undefined) {
      clearTimeout(timer.current);
      timer.current = undefined;
    }
  }, []);

  // A new server value ends the wait. Keyed on `serverValue` alone: adding
  // `pending` would clear the overlay in the very tick it was set.
  useEffect(() => {
    stopTimer();
    setPending(null);
  }, [serverValue, stopTimer]);

  useEffect(() => stopTimer, [stopTimer]);

  const set = useCallback(
    (next: T) => {
      stopTimer();
      // Setting the value the server already holds has nothing to show
      // optimistically — and would arm a timeout that could never be resolved,
      // because the server's echo would be an identical value and so would not
      // re-run the effect above.
      if (isEqualRef.current(next, serverRef.current)) {
        setPending(null);
        return;
      }
      setPending({ value: next });
      timer.current = setTimeout(() => {
        timer.current = undefined;
        setPending(null);
        onTimeoutRef.current?.();
      }, timeoutMs);
    },
    [stopTimer, timeoutMs]
  );

  const clear = useCallback(() => {
    stopTimer();
    setPending(null);
  }, [stopTimer]);

  return {
    value: pending ? pending.value : serverValue,
    pending: pending !== null,
    set,
    clear,
  };
}
