// Cmd+K: the window's answer to the terminal's `g` jump-into-feature key.
//
// The terminal jumps by mode key because a mode card is the only destination
// it has. A window has more of them — a stepper, a saved-runs hub, a settings
// tab — so the palette is over the route registry itself, and every route that
// is a destination is reachable by typing part of its name.
//
// The matching is a pure function so the vitest suite covers the ranking
// without a DOM; the component is a list and a keydown handler over it.

import { APP_ROUTES, type AppRoute } from './routes';

export interface PaletteEntry {
  path: string;
  title: string;
  /** The sidebar group it sits under, for the row's second line. */
  group: string;
}

const GROUP_OF: [RegExp, string][] = [
  [/^\/humans\//, 'Humans'],
  [/^\/agents\//, 'Agents'],
  [/^\/ceremonies/, 'Ops'],
  [/^\/provenance/, 'Ops'],
  [/^\/settings\//, 'Settings'],
];

function groupOf(path: string): string {
  return GROUP_OF.find(([pattern]) => pattern.test(path))?.[1] ?? '';
}

/**
 * Every route the palette can jump to.
 *
 * Actions and dialogs are excluded: they are parity entries for buttons that
 * only mean something on the page that draws them, and jumping to one would
 * land nowhere.
 */
export function paletteEntries(routes: readonly AppRoute[] = APP_ROUTES): PaletteEntry[] {
  return routes
    .filter((route) => route.path.startsWith('/'))
    .map((route) => ({ path: route.path, title: route.title, group: groupOf(route.path) }));
}

/**
 * The entries matching what has been typed, best first.
 *
 * A title that starts with the query beats one that merely contains it, which
 * is what makes "se" reach Settings rather than Sessions. Beyond that the
 * registry's own order wins — it is the sidebar's order, so the list a person
 * has been looking at is the list they get.
 */
export function matchEntries(entries: PaletteEntry[], query: string): PaletteEntry[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return entries;
  const scored = entries
    .map((entry, index) => {
      const title = entry.title.toLowerCase();
      const rank = title.startsWith(needle) ? 0 : title.includes(needle) ? 1 : entry.path.includes(needle) ? 2 : -1;
      return { entry, rank, index };
    })
    .filter((row) => row.rank >= 0);
  scored.sort((a, b) => a.rank - b.rank || a.index - b.index);
  return scored.map((row) => row.entry);
}

/** Move a selection by `step`, stopping at both ends rather than wrapping. */
export function moveSelection(selected: number, step: number, count: number): number {
  if (count === 0) return 0;
  return Math.min(count - 1, Math.max(0, selected + step));
}

/** The chat's `/help` and the shell's `?` open one sheet, so they share an event. */
export const SHORTCUTS_EVENT = 'yeaboi:shortcuts';

export function openShortcuts(): void {
  window.dispatchEvent(new CustomEvent(SHORTCUTS_EVENT));
}

/**
 * Whether a bare key (`?`) should be swallowed by whatever is focused.
 *
 * Without this the sheet opens mid-sentence the first time anyone types a
 * question mark into the composer.
 */
export function isTyping(target: EventTarget | null): boolean {
  const element = target as HTMLElement | null;
  if (!element) return false;
  const tag = element.tagName?.toLowerCase();
  return tag === 'input' || tag === 'textarea' || tag === 'select' || element.isContentEditable === true;
}

export interface Shortcut {
  keys: string;
  what: string;
}

/**
 * The shortcuts sheet — what `?` and `/help` both show.
 *
 * Four terminal gestures are missing on purpose, and each is missing because
 * the constraint that produced it is gone: double-tap Space (a terminal cannot
 * see a key being released), Ctrl+V for a screenshot (a window pastes with the
 * paste key), Esc Esc (there is no ambiguity to disambiguate here), and the
 * too-small guard (a window has a minimum size).
 */
export function shortcuts(platform: string): Shortcut[] {
  const mod = platform === 'darwin' ? 'Cmd' : 'Ctrl';
  return [
    { keys: `${mod}+K`, what: 'go anywhere' },
    { keys: '?', what: 'this sheet' },
    { keys: 'Enter', what: 'send the message' },
    { keys: 'Shift+Enter', what: 'a new line' },
    { keys: '/', what: 'commands, at the start of the box' },
    { keys: `${mod}+V`, what: 'paste — text, or a screenshot straight into the chat' },
    { keys: `${mod}+Y`, what: 'call the ducks out' },
    { keys: 'Esc', what: 'close what is open · stop a running turn' },
  ];
}
