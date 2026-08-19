/**
 * Palette selection, shared by every surface.
 *
 * Switching a theme is a pure custom-property swap on `[data-theme]` (see
 * design/palette.css) — there is no runtime styling layer and nothing to
 * recompute, which is exactly why CSS Modules were chosen over CSS-in-JS.
 */

import { read, write } from './storage';

/** Palette ids, in cycle order. Mirrors the `[data-theme=…]` blocks in palette.css. */
export const THEMES = ['midnight', 'light', 'solarized', 'synthwave', 'forest'] as const;

export type Theme = (typeof THEMES)[number];

/**
 * Where the visitor's choice is persisted.
 *
 * **One key, for every surface.** There used to be three, and the split was
 * indefensible once you looked at it from the visitor's side: a palette chosen
 * on a retro board never reached the poker board, the gate, or the next export
 * anyone opened, so the same person kept re-picking the same theme.
 *
 * The string is a **compatibility contract** with pages already sitting on
 * people's disks — an export written last month carries a bundle that reads
 * this exact key and nothing else — so it keeps the name it had rather than
 * something prettier. Three Python tests grep the literal out of rendered HTML
 * for the same reason.
 */
export const THEME_KEYS = {
  site: 'yeaboi-export-theme',
  /** @deprecated Alias for {@link THEME_KEYS.site}; kept so exports and their tests read the same. */
  export: 'yeaboi-export-theme',
} as const;

/**
 * Keys the boards used before the split was collapsed.
 *
 * Read once, then written forward to the canonical key. Without this, shipping
 * the change would silently reset the palette of every retro and poker board
 * mid-ceremony — which is exactly the moment nobody wants to be surprised.
 */
export const LEGACY_THEME_KEYS = ['retro_theme', 'poker_theme'] as const;

/**
 * Swatch preview colours, per theme.
 *
 * This replaces a genuinely expensive hack: the old `buildSwatches` appended a
 * hidden `[data-theme]` div to the body, read `getComputedStyle` off it, and
 * removed it — forcing a synchronous layout **per swatch, every time the
 * picker opened**. The values are static; reading them from the live document
 * bought nothing.
 *
 * The obvious risk of a hand-maintained copy is drift, so it is not
 * hand-maintained in practice: `theme.test.ts` parses palette.css and asserts
 * every entry here matches, which fails the build if a palette changes.
 */
export const THEME_PREVIEW: Record<Theme, { bg: string; accent: string }> = {
  midnight: { bg: '#0e1013', accent: '#6e8cdc' },
  light: { bg: '#f6f8fa', accent: '#0969da' },
  solarized: { bg: '#002b36', accent: '#2ca9a0' },
  synthwave: { bg: '#1a1033', accent: '#ff5edb' },
  forest: { bg: '#0c1a12', accent: '#4cc38a' },
};

export function isTheme(value: unknown): value is Theme {
  return typeof value === 'string' && (THEMES as readonly string[]).includes(value);
}

function preferred(): Theme {
  const light =
    typeof window.matchMedia === 'function' && window.matchMedia('(prefers-color-scheme: light)').matches;
  return light ? 'light' : 'midnight';
}

/** Set the palette on `<html>`. Does not persist — see {@link setTheme}. */
export function applyTheme(theme: Theme): void {
  document.documentElement.setAttribute('data-theme', theme);
}

/**
 * Read the persisted palette, or `null` when unset or unknown.
 *
 * Adopts a legacy board key on the way past, once: a visitor mid-retro when
 * this shipped keeps the theme they picked, and the next write puts it under
 * the canonical key so the migration never runs again.
 */
export function storedTheme(key: string = THEME_KEYS.site): Theme | null {
  const raw = read('local', key);
  if (isTheme(raw)) return raw;
  if (key !== THEME_KEYS.site) return null;
  for (const legacy of LEGACY_THEME_KEYS) {
    const old = read('local', legacy);
    if (isTheme(old)) {
      write('local', key, old);
      return old;
    }
  }
  return null;
}

/** Apply and persist a palette. */
export function setTheme(theme: Theme, key: string = THEME_KEYS.site): void {
  applyTheme(theme);
  write('local', key, theme);
}

/**
 * Apply the persisted palette, falling back to the OS preference.
 *
 * Called before the first paint so a page never flashes midnight at someone who
 * chose light — including the join gate, which has no theme button of its own
 * but should still match the artifact the visitor is about to open.
 */
export function applyStoredTheme(key: string = THEME_KEYS.site): Theme {
  const theme = storedTheme(key) ?? preferred();
  applyTheme(theme);
  return theme;
}

/** The next palette in cycle order. */
export function nextTheme(theme: Theme): Theme {
  return THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length] as Theme;
}
