/**
 * Hold what is on screen while it is being replaced.
 *
 * Switching sprints swaps every card on the board at once. Rendering the new
 * set on the same frame is a cut; holding the old one for the length of its
 * exit gives the two halves somewhere to happen.
 *
 * Keyed rather than compared: the payload changes identity on every poll, and
 * a swap is a change of *which retro*, not of what is in one.
 */

import { useLayoutEffect, useRef, useState } from 'react';

export interface Swap<T> {
  /** What to render — the outgoing value while it leaves, the new one after. */
  payload: T;
  /** The key that payload belongs to. Use it to remount, so entrances replay. */
  key: string | number;
  /** True while the old value is on its way out. */
  leaving: boolean;
  /** False until the first swap, so nothing animates on load. */
  swapped: boolean;
}

export function useSwap<T>(payload: T, key: string | number, ms: number): Swap<T> {
  const [shown, setShown] = useState<{ payload: T; key: string | number }>({ payload, key });
  const [leaving, setLeaving] = useState(false);
  const swapped = useRef(false);
  // The value at the moment the swap started. `shown` is the one leaving, so
  // the incoming one has to be parked somewhere until the exit is over.
  const incoming = useRef(payload);
  incoming.current = payload;

  // Layout, not effect: an ordinary effect runs after paint, so the render that
  // changed the key would show one frame of the new board before the old one
  // has been given its exit.
  useLayoutEffect(() => {
    if (key === shown.key) {
      // Same retro, fresher cards — no swap, just keep up.
      setShown((current) => (current.payload === payload ? current : { payload, key }));
      return undefined;
    }
    swapped.current = true;
    setLeaving(true);
    const timer = window.setTimeout(() => {
      setShown({ payload: incoming.current, key });
      setLeaving(false);
    }, ms);
    return () => window.clearTimeout(timer);
  }, [key, payload, shown.key, ms]);

  return { payload: shown.payload, key: shown.key, leaving, swapped: swapped.current };
}
