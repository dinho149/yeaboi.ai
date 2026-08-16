/**
 * Where each face was, last time anyone looked.
 *
 * The table and the floor animate people between each other, and neither can
 * measure the other at the moment it needs to: when the floor opens, the two
 * chairs are already gone from the DOM by the time any effect runs, and when it
 * closes the panels go the same way. So each side records where its own faces
 * are on every layout, and the other side reads the last reading.
 *
 * Rectangles rather than elements, because the element the number describes has
 * usually been unmounted by the time it is used. Stale by one commit, which is
 * exactly the commit that is being animated away from.
 */

/** A face's box, in viewport coordinates. */
export interface FaceBox {
  x: number;
  y: number;
  width: number;
}

const seats = new Map<string, FaceBox>();
const floor = new Map<string, FaceBox>();

function box(el: Element): FaceBox {
  const r = el.getBoundingClientRect();
  return { x: r.left + r.width / 2, y: r.top + r.height / 2, width: r.width };
}

/** Called by the table for every seat it renders. */
export function rememberSeat(name: string, el: Element): void {
  seats.set(name, box(el));
}

/** Called by the floor for each duelist it renders. */
export function rememberFloor(name: string, el: Element): void {
  floor.set(name, box(el));
}

export function seatFace(name: string): FaceBox | undefined {
  return seats.get(name);
}

export function floorFace(name: string): FaceBox | undefined {
  return floor.get(name);
}
