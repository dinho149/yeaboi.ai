/**
 * Drag the dock around the frame's edge.
 *
 * The dock is a lifted section of the border, so it never detaches: it stays
 * stuck to one of three walls and slides along it. Dragging nearer to another
 * wall hops it there and reorients it — a row along the bottom, a column up
 * either side. The top is excluded because the masthead is there.
 *
 * Docking is recomputed on every pointer frame rather than on release, so the
 * dock is never floating mid-gesture. The transition is off while held, since
 * tracking a pointer through one reads as lag, and returns on release so a
 * clamp at the end of a wall glides.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

export type DockEdge = 'bottom' | 'left' | 'right';

/** Clearance kept at each end so a fillet never runs onto the corner radius. */
const GUTTER = 28;

export interface DockDrag {
  ref: (node: HTMLElement | null) => void;
  edge: DockEdge;
  dragging: boolean;
  /** Distance along the current wall, in px from its start. */
  offset: number;
  onPointerDown(event: React.PointerEvent): void;
}

function nearestEdge(x: number, y: number, w: number, h: number): DockEdge {
  const toBottom = h - y;
  const min = Math.min(x, w - x, toBottom);
  if (min === toBottom) return 'bottom';
  return min === x ? 'left' : 'right';
}

export function useDockDrag(): DockDrag {
  const [edge, setEdge] = useState<DockEdge>('bottom');
  const [offset, setOffset] = useState(0);
  const [dragging, setDragging] = useState(false);
  const node = useRef<HTMLElement | null>(null);

  const ref = useCallback((el: HTMLElement | null) => {
    node.current = el;
  }, []);

  /** Put the dock on the wall nearest (x, y), centred under the pointer. */
  const dock = useCallback((x: number, y: number) => {
    const el = node.current;
    if (!el) return;
    const host = (el.offsetParent as HTMLElement | null) ?? document.documentElement;
    const w = host.clientWidth;
    const h = host.clientHeight;
    const next = nearestEdge(x, y, w, h);
    // Along the bottom the dock is a row measured by width; up a side it is a
    // column measured by height.
    const span = next === 'bottom' ? el.offsetWidth : el.offsetHeight;
    const track = next === 'bottom' ? w : h;
    const along = next === 'bottom' ? x : y;
    const max = Math.max(GUTTER, track - span - GUTTER);
    setEdge(next);
    setOffset(Math.min(Math.max(along - span / 2, GUTTER), max));
  }, []);

  const onPointerDown = useCallback((event: React.PointerEvent) => {
    // Controls inside keep their click; only the dock's own ground is a handle.
    if ((event.target as HTMLElement).closest('button, a, input, select, [role="button"]')) return;
    setDragging(true);
    (event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId);
  }, []);

  useEffect(() => {
    if (!dragging) return;
    const move = (event: PointerEvent): void => dock(event.clientX, event.clientY);
    const end = (): void => setDragging(false);
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', end);
    window.addEventListener('pointercancel', end);
    return () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', end);
      window.removeEventListener('pointercancel', end);
    };
  }, [dragging, dock]);

  // A resized window can leave the dock parked past the end of its wall.
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

  return { ref, edge, dragging, offset, onPointerDown };
}
