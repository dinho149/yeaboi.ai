// Where the macOS Dock is, so the duck can stand on it rather than beside it.
//
// There is no API for this: the position comes from System Events over the
// Accessibility API, which means an osascript round-trip and a string to parse.
// The parse is here, away from anything that imports electron, because the
// interesting cases are all textual — a refused permission, a hidden dock, a
// localised error — and each of them must degrade to "no dock", never to a duck
// standing at NaN.

export interface DockRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** The AppleScript that returns `x,y,w,h` for the Dock's tile list. */
export const DOCK_SCRIPT: readonly string[] = [
  'tell application "System Events" to tell process "Dock"',
  'set p to position of list 1',
  'set s to size of list 1',
  'return ((item 1 of p) as text) & "," & ((item 2 of p) as text) & "," & ((item 1 of s) as text) & "," & ((item 2 of s) as text)',
  'end tell',
];

/** Parse that script's stdout. Anything unexpected is `null` — floor only. */
export function parseDockRect(stdout: string): DockRect | null {
  const nums = String(stdout).trim().split(',').map(Number);
  if (nums.length !== 4 || nums.some((value) => !Number.isFinite(value))) return null;
  const [x, y, w, h] = nums as [number, number, number, number];
  // A zero-sized dock is a hidden one; standing on it would park the duck on an
  // edge that is not there.
  if (w <= 0 || h <= 0) return null;
  return { x, y, w, h };
}

/** The dock's geometry in window-local coordinates, as the pet renderer wants
 *  it — or `{present: false}`, which it draws as a plain floor. */
export function dockConfig(rect: DockRect | null, origin: { x: number; y: number }): Record<string, unknown> {
  if (!rect) return { present: false };
  return { x: rect.x - origin.x, top: rect.y - origin.y, w: rect.w, h: rect.h, present: true };
}
