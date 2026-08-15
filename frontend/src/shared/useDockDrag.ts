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
 * so it never runs onto the screen's corner radius.
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
  y: number;
  onPointerDown(event: PointerEvent): void;
}

interface Box {
  w: number;
  h: number;
  dw: number;
  dh: number;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

function place(x: number, { w, h, dw, dh }: Box): { x: number; y: number } {
  return { x: clamp(x, GUTTER, Math.max(GUTTER, w - dw - GUTTER)), y: h - dh };
}

export function useDockDrag(): DockDrag {
  const [position, setPosition] = useState({ x: 0, y: 0 });
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
    return { w: host.clientWidth, h: host.clientHeight, dw: el.offsetWidth, dh: el.offsetHeight };
  }, []);

  const settle = useCallback(
    (x: number) => {
      const b = box();
      if (!b) return;
      left.current = x;
      setPosition(place(x, b));
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

  // A resized window can leave the dock parked past the end of its wall — and
  // so can the dock's own height changing, which is what `y` is measured from.
  useEffect(() => {
    const onResize = (): void => {
      if (left.current !== null) settle(left.current);
    };
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [settle]);

  return { ref, dragging, placed, x: position.x, y: position.y, onPointerDown };
}
