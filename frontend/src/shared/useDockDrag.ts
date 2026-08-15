/**
 * Drag the dock around the frame's edge.
 *
 * The dock is a lifted section of the border, so it never detaches: it stays
 * stuck to one of three walls and slides along it. The top is excluded because
 * the masthead is there.
 *
 * ## Why a perimeter coordinate rather than an edge plus an offset
 *
 * Positioning per wall means `left`/`top`/`bottom` swapping between a length
 * and `auto` as the wall changes, and neither the swap nor `auto` interpolates
 * — so crossing a corner jumped. The dock is placed instead by one scalar `t`,
 * its distance along the frame's perimeter, mapped to a translation. `t` moves
 * continuously as the pointer does, including around a corner, so the dock
 * crawls onto the next wall and a CSS transition on `transform` covers the
 * release.
 *
 * The path runs top-left → down the left wall → along the bottom → up the
 * right wall. `edge` is derived from `t` and only drives orientation.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

export type DockEdge = 'bottom' | 'left' | 'right';

/** Clearance kept at each end so a fillet never runs onto the corner radius. */
const GUTTER = 28;

export interface DockPlacement {
  edge: DockEdge;
  x: number;
  y: number;
}

export interface DockDrag {
  ref: (node: HTMLElement | null) => void;
  dragging: boolean;
  placement: DockPlacement;
  onPointerDown(event: React.PointerEvent): void;
}

interface Box {
  w: number;
  h: number;
  dw: number;
  dh: number;
}

/** Nearest point on the three-wall path, as a distance from the top-left. */
function project(px: number, py: number, { w, h }: Box): number {
  const toLeft = px;
  const toRight = w - px;
  const toBottom = h - py;
  const min = Math.min(toLeft, toRight, toBottom);
  if (min === toLeft) return clamp(py, 0, h);
  if (min === toBottom) return h + clamp(px, 0, w);
  return h + w + (h - clamp(py, 0, h));
}

/** Where the dock sits for a perimeter distance, clamped to stay on one wall. */
function place(t: number, box: Box): DockPlacement {
  const { w, h, dw, dh } = box;
  if (t < h) {
    const y = clamp(t - dh / 2, GUTTER, Math.max(GUTTER, h - dh - GUTTER));
    return { edge: 'left', x: 0, y };
  }
  if (t < h + w) {
    const x = clamp(t - h - dw / 2, GUTTER, Math.max(GUTTER, w - dw - GUTTER));
    return { edge: 'bottom', x, y: h - dh };
  }
  const along = h - (t - h - w);
  const y = clamp(along - dh / 2, GUTTER, Math.max(GUTTER, h - dh - GUTTER));
  return { edge: 'right', x: w - dw, y };
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

export function useDockDrag(): DockDrag {
  const [placement, setPlacement] = useState<DockPlacement>({ edge: 'bottom', x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);
  const node = useRef<HTMLElement | null>(null);
  const distance = useRef<number | null>(null);

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
    (t: number) => {
      const b = box();
      if (!b) return;
      distance.current = t;
      setPlacement(place(t, b));
    },
    [box],
  );

  const onPointerDown = useCallback((event: React.PointerEvent) => {
    // Controls inside keep their click; only the dock's own ground is a handle.
    if ((event.target as HTMLElement).closest('button, a, input, select, [role="button"]')) return;
    setDragging(true);
    (event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId);
  }, []);

  // Rest at the bottom-right, where the dock has always sat.
  useEffect(() => {
    const b = box();
    if (!b || distance.current !== null) return;
    settle(b.h + b.w - GUTTER - b.dw / 2);
  }, [box, settle]);

  useEffect(() => {
    if (!dragging) return;
    const move = (event: PointerEvent): void => {
      const b = box();
      if (b) settle(project(event.clientX, event.clientY, b));
    };
    const end = (): void => setDragging(false);
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', end);
    window.addEventListener('pointercancel', end);
    return () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', end);
      window.removeEventListener('pointercancel', end);
    };
  }, [dragging, box, settle]);

  // A resized window can leave the dock parked past the end of its wall.
  useEffect(() => {
    const onResize = (): void => {
      if (distance.current !== null) settle(distance.current);
    };
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [settle]);

  return { ref, dragging, placement, onPointerDown };
}
