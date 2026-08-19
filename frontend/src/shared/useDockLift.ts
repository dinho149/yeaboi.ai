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

/** The most it will leave the floor, as a share of the screen's height. */
const LIFT = 0.03;
/** Sideways room, as a share of the screen's width, before the band goes slack. */
const SPREAD = 0.5;

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
        // Everything is a ratio of the screen and of the room the dock has above
        // it, so the tether spans the whole board on any display rather than
        // dying a few hundred pixels up.
        const climb = Math.min(1, above / Math.max(1, box.top));
        // Distance to the dock itself, not to its centre: a wide dock should
        // not feel further away to somebody standing over its edge.
        const sideways = Math.max(0, box.left - event.clientX, event.clientX - box.right);
        const spread = Math.max(0, 1 - sideways / (window.innerWidth * SPREAD));
        // Straight through: the dock is at whatever fraction of the way up the
        // pointer is, so half-way up the screen is half the lift and the full
        // travel is only spent at the top. A curve that front-loads it — which
        // this was — is at its maximum by the middle of the screen and has
        // nothing left for the rest.
        apply(climb * window.innerHeight * LIFT * spread);
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
