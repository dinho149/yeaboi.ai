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

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';

export type DockEdge = 'bottom' | 'left' | 'right';

/** Clearance kept at each end so a fillet never runs onto the corner radius. */
const GUTTER = 28;

export interface DockPlacement {
  edge: DockEdge;
  x: number;
  y: number;
}

/** A stretch of the dock that lies on one wall. */
export interface DockRun extends DockPlacement {
  /** Indices of the controls in this stretch, in order. */
  items: number[];
}

export interface DockDrag {
  ref: (node: HTMLElement | null) => void;
  dragging: boolean;
  /** True for one eased beat while the dock changes wall. */
  turning: boolean;
  /** False until the dock has been measured and put on a wall. */
  placed: boolean;
  placement: DockPlacement;
  /**
   * The dock split by wall. One run while it lies along a single wall — the
   * usual case — and two while it is rounding a corner, so some controls have
   * turned the corner and the rest have not.
   */
  runs: DockRun[];
  /** Report a control's extent along the path, so the split knows where it falls. */
  measure(index: number, extent: number): void;
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

/**
 * Where a stretch of the dock sits for a perimeter distance.
 *
 * The stretch's *leading* edge goes at `t`, not its centre: a run that has
 * turned a corner has to butt against the one behind it, and centring each on
 * its own cursor overlaps them. `along` is the run's own extent — the whole
 * dock's when there is only one.
 */
function place(t: number, box: Box, along: number, across: number): DockPlacement {
  const { w, h } = box;
  if (t < h) {
    return { edge: 'left', x: 0, y: clamp(t, GUTTER, Math.max(GUTTER, h - along - GUTTER)) };
  }
  if (t < h + w) {
    return { edge: 'bottom', x: clamp(t - h, GUTTER, Math.max(GUTTER, w - along - GUTTER)), y: h - across };
  }
  const up = h - (t - h - w) - along;
  return { edge: 'right', x: w - across, y: clamp(up, GUTTER, Math.max(GUTTER, h - along - GUTTER)) };
}

/**
 * Walk the controls along the path from `t`, cutting a new run each time the
 * wall changes. With every control on one wall this returns a single run and
 * the dock renders exactly as it always has.
 */
function split(t: number, box: Box, count: number, extents: readonly number[]): DockRun[] {
  if (count <= 0) return [];
  // Which wall each control lands on, walking the path from `t`.
  const walls: DockEdge[] = [];
  const starts: number[] = [];
  let cursor = t;
  for (let i = 0; i < count; i += 1) {
    walls.push(place(cursor, box, 0, 0).edge);
    starts.push(cursor);
    cursor += extents[i] ?? 0;
  }
  const runs: DockRun[] = [];
  for (let i = 0; i < count; i += 1) {
    const last = runs[runs.length - 1];
    if (last && walls[i] === walls[last.items[0] as number]) last.items.push(i);
    else runs.push({ edge: walls[i] as DockEdge, x: 0, y: 0, items: [i] });
  }
  // Placed once the run is whole, so it is measured by its own extent.
  return runs.map((run) => {
    const along = run.items.reduce((sum, i) => sum + (extents[i] ?? 0), 0);
    const across = run.edge === 'bottom' ? box.dh : box.dw;
    return { ...run, ...place(starts[run.items[0] as number] as number, box, along, across) };
  });
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

export function useDockDrag(count = 0): DockDrag {
  const [placement, setPlacement] = useState<DockPlacement>({ edge: 'bottom', x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);
  const [turning, setTurning] = useState(false);
  const [placed, setPlaced] = useState(false);
  const placedRef = useRef(false);
  const node = useRef<HTMLElement | null>(null);
  const distance = useRef<number | null>(null);
  const extents = useRef<number[]>([]);
  const [runs, setRuns] = useState<DockRun[]>([]);
  /** Where along the dock it was grabbed, so it does not snap under the cursor. */
  const grab = useRef(0);
  const edgeRef = useRef<DockEdge>('bottom');
  const turnTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

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
      // Two passes: which wall it lands on decides which of its dimensions runs
      // along that wall.
      const wall = place(t, b, 0, 0).edge;
      const next = place(t, b, wall === 'bottom' ? b.dw : b.dh, wall === 'bottom' ? b.dh : b.dw);
      setRuns(split(t, b, count, extents.current));
      if (!placedRef.current) {
        placedRef.current = true;
        // Visible now, animated from the next frame. Enabling the transition in
        // the same commit that sets the first transform makes the browser
        // animate out of the untranslated corner.
        requestAnimationFrame(() => setPlaced(true));
      }
      if (next.edge !== edgeRef.current) {
        edgeRef.current = next.edge;
        // Changing wall is a reorientation, not a slide, so it is the one move
        // that gets eased. Everything else tracks the pointer exactly.
        setTurning(true);
        if (turnTimer.current) clearTimeout(turnTimer.current);
        turnTimer.current = setTimeout(() => setTurning(false), 220);
      }
      setPlacement(next);
    },
    [box],
  );

  const onPointerDown = useCallback((event: React.PointerEvent) => {
    // Controls inside keep their click; only the dock's own ground is a handle.
    if ((event.target as HTMLElement).closest('button, a, input, select, [role="button"]')) return;
    const el = event.currentTarget as HTMLElement;
    const host = (el.offsetParent as HTMLElement | null) ?? document.documentElement;
    const b = { w: host.clientWidth, h: host.clientHeight, dw: el.offsetWidth, dh: el.offsetHeight };
    // The dock keeps its position under the cursor: the gap between where it
    // sits and where it was grabbed is held for the whole drag.
    grab.current = (distance.current ?? 0) - project(event.clientX, event.clientY, b);
    setDragging(true);
    el.setPointerCapture?.(event.pointerId);
  }, []);

  // Rest at the bottom-right, where the dock has always sat. Before paint, or
  // the first frame renders it in the top-left corner and it visibly flies
  // across the board on every load.
  useLayoutEffect(() => {
    const b = box();
    if (!b || distance.current !== null) return;
    settle(b.h + b.w - GUTTER - b.dw);
  }, [box, settle]);

  useEffect(() => {
    if (!dragging) return;
    const move = (event: PointerEvent): void => {
      const b = box();
      if (b) settle(project(event.clientX, event.clientY, b) + grab.current);
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

  const measure = useCallback((index: number, extent: number) => {
    if (extents.current[index] === extent) return;
    extents.current[index] = extent;
    if (distance.current !== null) settle(distance.current);
  }, [settle]);

  return { ref, dragging, turning, placed, placement, runs, measure, onPointerDown };
}
