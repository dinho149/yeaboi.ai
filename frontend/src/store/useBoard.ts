/**
 * React bindings for {@link BoardStore}.
 *
 * `useSyncExternalStore` has one rule that bites everybody: **`getSnapshot` must
 * return a referentially stable value** when nothing changed. Return a fresh
 * object or array each call and React re-renders forever, because it compares
 * with `Object.is`. A selector like `s => s.cards.filter(…)` does exactly that.
 *
 * {@link useBoardSelector} therefore memoises the selector's *result*: it
 * re-runs the selector when the store notifies, and hands back the previous
 * result when the new one is equal under the supplied comparator. Callers get
 * to write the natural selector and still not spin.
 */

import { useCallback, useRef, useSyncExternalStore } from 'react';

import type { BoardStore, Revisioned } from './boardStore';

/**
 * The whole snapshot. Fine for a small tree; prefer a selector for a big one.
 *
 * Two arguments, not three: `preact/compat`'s `useSyncExternalStore` has no
 * `getServerSnapshot` parameter, because nothing here is server-rendered. These
 * pages ship as a bundle plus a JSON island, so the first client render is the
 * only render — there is no hydration mismatch to guard against.
 */
export function useBoardSnapshot<S extends Revisioned>(store: BoardStore<S>): S | null {
  return useSyncExternalStore(store.subscribe, store.getSnapshot);
}

/**
 * Subscribe to one slice of the snapshot.
 *
 * @param store the board store
 * @param selector pure projection of the snapshot; may return objects/arrays
 * @param isEqual comparator for the projected value — default `Object.is`.
 *   Pass a shallow-array comparator when the selector rebuilds a list every
 *   time, which is the common case for "the cards in this column".
 *
 * The selector receives `null` before the first snapshot lands, so every caller
 * has to state what an empty board looks like rather than crashing on it.
 */
export function useBoardSelector<S extends Revisioned, T>(
  store: BoardStore<S>,
  selector: (snapshot: S | null) => T,
  isEqual: (a: T, b: T) => boolean = Object.is
): T {
  // Refs rather than state: updating these must not itself schedule a render.
  const cache = useRef<{ has: boolean; value: T }>({ has: false, value: undefined as T });
  // Kept in refs so a caller passing inline arrow functions (the normal way to
  // write this) does not invalidate the memo on every render.
  const selectorRef = useRef(selector);
  const equalRef = useRef(isEqual);
  selectorRef.current = selector;
  equalRef.current = isEqual;

  const getSelection = useCallback((): T => {
    const next = selectorRef.current(store.getSnapshot());
    if (cache.current.has && equalRef.current(cache.current.value, next)) {
      return cache.current.value;
    }
    cache.current = { has: true, value: next };
    return next;
  }, [store]);

  return useSyncExternalStore(store.subscribe, getSelection);
}

/** Shallow array comparison, for selectors that rebuild a list each call. */
export function shallowArrayEqual<T>(a: readonly T[], b: readonly T[]): boolean {
  if (a === b) return true;
  if (a.length !== b.length) return false;
  return a.every((item, index) => Object.is(item, b[index]));
}
