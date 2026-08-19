/**
 * Slide the dock along the bottom of the screen.
 *
 * The dock is a lifted section of the bottom edge, so it never detaches: it
 * stays on that wall and moves along it. It used to be able to climb the left
 * and right walls too, which cost a perimeter coordinate, a two-run split
 * around each corner, and a measurement per control so the split knew where
 * each one fell — all of it to reach two positions nobody wanted a toolbar in.
 *
 * What is left is one number: `x`, the dock's distance from the left, clamped
 * so it never runs onto the screen's corner radius. The vertical went with it:
 * the dock is pinned to the bottom in CSS, so it grows upward on its own when a
 * panel opens inside it, with nothing to recompute.
 */

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';

/** Clearance kept at each end, so the dock stays clear of the rounded corner. */
const GUTTER = 28;

export interface DockDrag {
  ref: (node: HTMLElement | null) => void;
  dragging: boolean;
  /** False until the dock has been measured and placed. */
  placed: boolean;
  x: number;
  onPointerDown(event: PointerEvent): void;
}

interface Box {
  w: number;
  dw: number;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

function place(x: number, { w, dw }: Box): number {
  return clamp(x, GUTTER, Math.max(GUTTER, w - dw - GUTTER));
}

export function useDockDrag(): DockDrag {
  const [x, setX] = useState(0);
  const [dragging, setDragging] = useState(false);
  const [placed, setPlaced] = useState(false);
  const placedRef = useRef(false);
  const node = useRef<HTMLElement | null>(null);
  const left = useRef<number | null>(null);
  /** Where along the dock it was grabbed, so it does not snap under the cursor. */
  const grab = useRef(0);

  const ref = useCallback((el: HTMLElement | null) => {
    node.current = el;
  }, []);

  const box = useCallback((): Box | null => {
    const el = node.current;
    if (!el) return null;
    const host = (el.offsetParent as HTMLElement | null) ?? document.documentElement;
    return { w: host.clientWidth, dw: el.offsetWidth };
  }, []);

  const settle = useCallback(
    (next: number) => {
      const b = box();
      if (!b) return;
      left.current = next;
      setX(place(next, b));
      if (!placedRef.current) {
        placedRef.current = true;
        // Visible now, animated from the next frame. Enabling the transition in
        // the same commit that sets the first transform makes the browser
        // animate out of the untranslated corner.
        requestAnimationFrame(() => setPlaced(true));
      }
    },
    [box],
  );

  const onPointerDown = useCallback((event: PointerEvent) => {
    // Controls inside keep their click; only the dock's own ground is a handle.
    if ((event.target as HTMLElement).closest('button, a, input, select, [role="button"]')) return;
    const el = event.currentTarget as HTMLElement;
    grab.current = (left.current ?? 0) - event.clientX;
    setDragging(true);
    el.setPointerCapture?.(event.pointerId);
  }, []);

  // Rest at the bottom-right, where the dock has always sat. Before paint, or
  // the first frame renders it in the top-left corner and it visibly flies
  // across the board on every load.
  useLayoutEffect(() => {
    const b = box();
    if (!b || left.current !== null) return;
    settle(b.w - b.dw - GUTTER);
  }, [box, settle]);

  useEffect(() => {
    if (!dragging) return;
    const move = (event: PointerEvent): void => settle(event.clientX + grab.current);
    const end = (): void => setDragging(false);
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', end);
    window.addEventListener('pointercancel', end);
    return () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', end);
      window.removeEventListener('pointercancel', end);
    };
  }, [dragging, settle]);

  // Two things can leave the dock hanging off the end of its wall: the window
  // resizing, and the dock itself growing when a panel opens inside it. The
  // second is why this observes the dock and not only the window — the clamp
  // then slides it back in, on the same transition the growth runs on.
  useEffect(() => {
    const reclamp = (): void => {
      if (left.current !== null) settle(left.current);
    };
    window.addEventListener('resize', reclamp);
    const el = node.current;
    const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(reclamp) : null;
    if (el) observer?.observe(el);
    return () => {
      window.removeEventListener('resize', reclamp);
      observer?.disconnect();
    };
  }, [settle]);

  return { ref, dragging, placed, x, onPointerDown };
}
