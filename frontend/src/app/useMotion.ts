/**
 * The motion layer, as hooks.
 *
 * Separate from `motion.ts` so the animation logic stays testable without a
 * renderer, and so a non-Preact surface (the deck, a future Tauri window) can
 * use the same tweens.
 */

import { useEffect, useRef } from 'preact/hooks';

import { enter, enterList } from './motion';

/** Animate an element in when it mounts, and when `key` changes. */
export function useEnter<T extends Element>(key: unknown = null) {
  const ref = useRef<T>(null);
  useEffect(() => enter(ref.current), [key]);
  return ref;
}

/** Animate `selector` children in, staggered, whenever `key` changes. */
export function useEnterList<T extends Element>(selector: string, key: unknown = null) {
  const ref = useRef<T>(null);
  // The cleanup GSAP hands back reverts every tween this created, so a route
  // change mid-animation cannot leave a half-faded row behind.
  useEffect(() => enterList(ref.current, selector), [selector, key]);
  return ref;
}
