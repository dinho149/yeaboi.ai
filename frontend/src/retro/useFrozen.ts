/**
 * Hold a value still while something is in progress.
 *
 * The board is a live surface: a snapshot lands whenever anyone does anything.
 * That is fine everywhere except mid-drag — a card list that reorders under a
 * pointer means the drop index you aimed at is not the one you get, and the
 * card visibly jumps out from under your finger.
 *
 * So the columns render a frozen copy for the duration of the drag. Nothing is
 * lost: the store keeps applying snapshots throughout, and the instant the drag
 * ends the current one appears. Freezing the *view*, not the store, is the
 * distinction that matters — a frozen store would drop the very snapshot that
 * confirms the move.
 */

import { useRef } from 'react';

export function useFrozen<T>(value: T, frozen: boolean): T {
  const held = useRef(value);
  if (!frozen) held.current = value;
  return held.current;
}
