/**
 * Drag the dock around the frame's edge.
 *
 * The dock is a lifted section of the border, so it never floats: it is always
 * on one of three edges and travels along it. A drag moves it freely, and on
 * release it re-docks to whichever edge the pointer is nearest, reorienting —
 * a row along the bottom, a column up either side.
 *
 * The top edge is excluded because the masthead is there.
 *
 * While a drag is live the element tracks the pointer with no transition;
 * anything else feels like lag. On release the transition returns, so the
 * re-dock is a glide rather than a jump.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

export type DockEdge = 'bottom' | 'left' | 'right';

/** Clearance kept at each end so a fillet never runs onto the corner radius. */
const GUTTER = 28;

interface Free {
  x: number;
  y: number;
}

export interface DockDrag {
  ref: (node: HTMLElement | null) => void;
  edge: DockEdge;
  dragging: boolean;
  /** Distance along the current edge, in px from its start. */
  offset: number;
  free: Free | null;
  onPointerDown(event: React.PointerEvent): void;
}

function nearestEdge(cx: number, cy: number, w: number, h: number): DockEdge {
  const toLeft = cx;
  const toRight = w - cx;
  const toBottom = h - cy;
  const min = Math.min(toLeft, toRight, toBottom);
  if (min === toBottom) return 'bottom';
  return min === toLeft ? 'left' : 'right';
}

export function useDockDrag(): DockDrag {
  const [edge, setEdge] = useState<DockEdge>('bottom');
  const [offset, setOffset] = useState(0);
  const [free, setFree] = useState<Free | null>(null);
  const [dragging, setDragging] = useState(false);

  const node = useRef<HTMLElement | null>(null);
  const grab = useRef({ dx: 0, dy: 0 });

  const ref = useCallback((el: HTMLElement | null) => {
    node.current = el;
  }, []);

  const settle = useCallback((cx: number, cy: number) => {
    const el = node.current;
    if (!el) return;
    const host = (el.offsetParent as HTMLElement | null) ?? document.documentElement;
    const w = host.clientWidth;
    const h = host.clientHeight;
    const next = nearestEdge(cx, cy, w, h);
    // Along the bottom the dock is a row measured by width; up a side it is a
    // column measured by height, and the box has already been re-laid out by
    // the time this runs on the next drag.
    const span = next === 'bottom' ? el.offsetWidth : el.offsetHeight;
    const track = next === 'bottom' ? w : h;
    const along = next === 'bottom' ? cx : cy;
    const max = Math.max(GUTTER, track - span - GUTTER);
    setEdge(next);
    setOffset(Math.min(Math.max(along - span / 2, GUTTER), max));
    setFree(null);
  }, []);

  const onPointerDown = useCallback((event: React.PointerEvent) => {
    // Controls inside keep their click; only the dock's own ground is a handle.
    if ((event.target as HTMLElement).closest('button, a, input, select, [role="button"]')) return;
    const el = event.currentTarget as HTMLElement;
    const box = el.getBoundingClientRect();
    grab.current = { dx: event.clientX - box.left, dy: event.clientY - box.top };
    setFree({ x: box.left, y: box.top });
    setDragging(true);
    el.setPointerCapture?.(event.pointerId);
  }, []);

  useEffect(() => {
    if (!dragging) return;
    let last = { x: 0, y: 0 };
    const move = (event: PointerEvent): void => {
      last = { x: event.clientX, y: event.clientY };
      setFree({ x: event.clientX - grab.current.dx, y: event.clientY - grab.current.dy });
    };
    const end = (): void => {
      setDragging(false);
      settle(last.x, last.y);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', end);
    window.addEventListener('pointercancel', end);
    return () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', end);
      window.removeEventListener('pointercancel', end);
    };
  }, [dragging, settle]);

  // A resized window can leave the dock parked past the end of its edge.
  useEffect(() => {
    const onResize = (): void => {
      const el = node.current;
      if (!el) return;
      const host = (el.offsetParent as HTMLElement | null) ?? document.documentElement;
      const track = edge === 'bottom' ? host.clientWidth : host.clientHeight;
      const span = edge === 'bottom' ? el.offsetWidth : el.offsetHeight;
      setOffset((current) => Math.min(current, Math.max(GUTTER, track - span - GUTTER)));
    };
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [edge]);

  return { ref, edge, dragging, offset, free, onPointerDown };
}
