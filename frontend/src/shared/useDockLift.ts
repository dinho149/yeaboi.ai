/**
 * The give in the dock while it is being dragged.
 *
 * It travels one wall, so the pointer can lead it somewhere it cannot go. Held
 * rigidly to that wall the drag feels like the dock is bolted to it; on a
 * tether it comes up after the pointer at a fraction of the distance, and the
 * fraction thins out the further the pointer goes — pull hard and it gives less
 * and less, the way a rubber band does. Letting go drops it back with the
 * overshoot on `.dockPlaced`.
 *
 * Only vertical, and only upward. Sideways *is* the drag, and the wall below is
 * where the dock lives.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

/** How much of the pointer's height above the dock it takes for itself. */
const RISE = 0.16;
/** The most it will ever leave the floor. */
const CEILING = 16;
/** Never so short that a small viewport zeroes the tether out. */
const MIN_REACH = 240;

export interface DockLift {
  /** Pixels above its resting place, never negative. */
  lift: number;
}

export function useDockLift(node: () => HTMLElement | null, dragging: boolean): DockLift {
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
    if (!dragging || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return undefined;

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
        // The reach is the room above the dock, so the tether is still taut at
        // the top bar rather than going slack a third of the way up. It thins
        // the whole way, which is what makes pulling harder give less.
        const reach = Math.max(0, 1 - away / Math.max(MIN_REACH, box.top));
        apply(Math.min(CEILING, above * RISE) * reach);
      });
    };

    window.addEventListener('pointermove', onMove, { passive: true });
    // Back on the wall the moment it is let go.
    return () => {
      window.removeEventListener('pointermove', onMove);
      window.cancelAnimationFrame(frame.current);
      frame.current = 0;
      setLift(0);
    };
  }, [node, dragging, apply]);

  return { lift };
}
