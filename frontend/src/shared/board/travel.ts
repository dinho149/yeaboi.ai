/**
 * Moving a person from one place on the board to another.
 *
 * A live board rearranges itself constantly — someone leaves a row to take the
 * floor, a row closes the gap, they come back. Left to the browser every one of
 * those is a teleport, and a teleport in the middle of a page that is otherwise
 * moving smoothly reads as a fault rather than as an update.
 *
 * ## Why a registry rather than two refs
 *
 * The two ends are never mounted at the same time. When the floor opens, the
 * chair is already gone from the DOM by the time any effect runs; when it
 * closes, so is the panel. So each side records where its own faces are on
 * every layout, and the other side reads the last reading — one commit stale,
 * which is exactly the commit being animated away from.
 *
 * Boxes rather than elements, because the element a box describes has usually
 * been unmounted by the time it is used.
 */

import { useLayoutEffect, useRef } from 'react';

/** How long a move takes, and the curve it takes. */
export const TRAVEL_MS = 460;
export const TRAVEL_EASE = 'cubic-bezier(0.32, 0.94, 0.3, 1)';

/** A face's box, in viewport coordinates. */
export interface FaceBox {
  x: number;
  y: number;
  width: number;
}

/**
 * Where faces were last seen, by place and by name.
 *
 * `place` is the caller's own word for one end of a journey — a board with a
 * table and a floor uses `'table'` and `'floor'`. Two surfaces never share a
 * place name by accident because each passes its own.
 */
const seen = new Map<string, Map<string, FaceBox>>();

function shelf(place: string): Map<string, FaceBox> {
  const found = seen.get(place);
  if (found) return found;
  const made = new Map<string, FaceBox>();
  seen.set(place, made);
  return made;
}

export function boxOf(el: Element): FaceBox {
  const r = el.getBoundingClientRect();
  return { x: r.left + r.width / 2, y: r.top + r.height / 2, width: r.width };
}

/** Record where one of this place's faces is. Call it on every layout. */
export function remember(place: string, name: string, el: Element): void {
  shelf(place).set(name, boxOf(el));
}

/** The last box recorded for a name at a place, if there is one. */
export function lastSeen(place: string, name: string): FaceBox | undefined {
  return shelf(place).get(name);
}

/** Animate `el` as though it had come from `from`. Silent if it cannot. */
export function travelFrom(el: HTMLElement, from: FaceBox | undefined, opts: { fade?: boolean } = {}): void {
  if (!from || typeof el.animate !== 'function') return;
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const here = el.getBoundingClientRect();
  if (!here.width) return;
  const dx = from.x - (here.left + here.width / 2);
  const dy = from.y - (here.top + here.height / 2);
  if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5) return;
  const scale = Math.min(3, Math.max(0.2, from.width / here.width));
  el.animate(
    [
      { transform: `translate(${dx}px, ${dy}px) scale(${scale})`, ...(opts.fade ? { opacity: 0 } : {}) },
      { transform: 'none', ...(opts.fade ? { opacity: 1 } : {}) },
    ],
    { duration: TRAVEL_MS, easing: TRAVEL_EASE }
  );
}

/**
 * Fly one element in from wherever its person last was somewhere else.
 *
 * For the arriving side of a journey: a panel that has just appeared, holding
 * someone who was in a row a moment ago. Runs once per name — a re-render while
 * they are still there must not send them back and out again.
 */
export function useArrival(
  el: { current: HTMLElement | null },
  opts: { place: string; from: string; name: string; alsoFade?: { current: HTMLElement | null } }
): void {
  const flown = useRef('');
  const { place, from, name, alsoFade } = opts;

  useLayoutEffect(() => {
    const node = el.current;
    if (!name || !node) return;
    // Recorded every layout, so the other end can fly them home again.
    remember(place, name, node);
    if (flown.current === name) return;
    flown.current = name;
    travelFrom(node, lastSeen(from, name));
    // What surrounds them arrives rather than travelling: the person moved, the
    // furniture did not.
    alsoFade?.current?.animate([{ opacity: 0 }, { opacity: 1 }], {
      duration: TRAVEL_MS,
      easing: 'ease-out',
    });
  }, [el, place, from, name, alsoFade]);
}

/**
 * Keep a row of people in step as its membership changes.
 *
 * Every child carrying `data-travel="<name>"` is animated from where it was to
 * where it now is; one that was not in the row before comes in from `from`
 * instead — the other end of a journey it has just finished.
 *
 * Keyed on the membership, not on every render: a live board re-renders once a
 * second and measuring a row of ten each time forces a layout for nothing.
 */
export function useRowChoreography(
  row: { current: HTMLElement | null },
  opts: { place: string; from: string; members: string }
): void {
  const was = useRef(new Map<string, FaceBox>());
  const { place, from, members } = opts;

  useLayoutEffect(() => {
    const list = row.current;
    if (!list) return;
    const now = new Map<string, FaceBox>();

    for (const item of list.querySelectorAll<HTMLElement>('[data-travel]')) {
      const name = item.dataset['travel'] ?? '';
      const face = item.querySelector('[data-face]') ?? item;
      const here = boxOf(item);
      const known = was.current.get(name);
      now.set(name, here);
      remember(place, name, face);
      // A seat that was already here slides; one that has just arrived comes
      // from the place it was, and fades as it does.
      travelFrom(item, known ?? lastSeen(from, name), { fade: !known });
    }

    was.current = now;
  }, [row, place, from, members]);
}
