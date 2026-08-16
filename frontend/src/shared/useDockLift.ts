/**
 * The dock leans toward the pointer, and drops back with a bounce.
 *
 * A fraction of the pointer's height above the dock, attenuated by how far away
 * it is — so the dock rises as you approach, peaks when you are near it, and
 * ignores you entirely from across the board. The two together are the whole
 * effect: `rise` alone would have the dock most excited about a pointer at the
 * top of the screen, which is the one place it should be indifferent to.
 *
 * Only vertical, and only upward. The dock's horizontal position is a thing the
 * user placed deliberately, and nothing else should move it.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

/** How much of the pointer's height above the dock it takes for itself. */
const RISE = 0.16;
/** The most it will ever leave the floor. */
const CEILING = 16;
/** Beyond this, the pointer is somebody else's business. */
const REACH = 320;

export interface DockLift {
  /** Pixels above its resting place, never negative. */
  lift: number;
  /**
   * Off the floor. While it is, the transform must not be eased — easing a
   * pointer-follow reads as lag; the spring belongs to the way back down.
   */
  following: boolean;
}

export function useDockLift(node: () => HTMLElement | null, enabled = true): DockLift {
  const [lift, setLift] = useState(0);
  const frame = useRef(0);

  const apply = useCallback((raw: number) => {
    // Half a pixel is below what a transform can show and above what a stream
    // of pointer events would otherwise re-render on. Snapped rather than
    // merely ignored: a dock resting a third of a pixel off the floor is still
    // "following", and would never take the spring back down.
    const next = raw < 0.5 ? 0 : raw;
    setLift((current) => (Math.abs(current - next) < 0.5 ? current : next));
  }, []);

  useEffect(() => {
    if (!enabled || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return undefined;

    const onMove = (event: PointerEvent): void => {
      if (frame.current) return;
      frame.current = window.requestAnimationFrame(() => {
        frame.current = 0;
        const el = node();
        if (!el) return;
        const box = el.getBoundingClientRect();
        const above = box.top - event.clientY;
        if (above <= 0) {
          apply(0);
          return;
        }
        // Distance to the dock itself, not to its centre: a wide dock should
        // not feel further away to somebody standing over its edge.
        const sideways = Math.max(0, box.left - event.clientX, event.clientX - box.right);
        const away = Math.hypot(above, sideways);
        const reach = Math.max(0, 1 - away / REACH);
        apply(Math.min(CEILING, above * RISE) * reach);
      });
    };

    window.addEventListener('pointermove', onMove, { passive: true });
    return () => {
      window.removeEventListener('pointermove', onMove);
      window.cancelAnimationFrame(frame.current);
      frame.current = 0;
    };
  }, [node, enabled, apply]);

  return { lift, following: lift > 0 };
}
