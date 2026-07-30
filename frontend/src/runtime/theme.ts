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
 * Where each surface persists the visitor's choice.
 *
 * The export key is a **compatibility contract** with pages already sitting on
 * people's disks — an export written last month reads this exact string. The
 * board keys match what the old inline scripts used, so an in-flight ceremony
 * does not lose its theme when the React board ships.
 */
export const THEME_KEYS = {
  export: 'yeaboi-export-theme',
  retro: 'retro_theme',
  poker: 'poker_theme',
} as const;

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
  midnight: { bg: '#0b0c0e', accent: '#6e8cdc' },
  light: { bg: '#f6f8fa', accent: '#0969da' },
  solarized: { bg: '#002b36', accent: '#2ca9a0' },
  synthwave: { bg: '#1a1033', accent: '#ff5edb' },
  forest: { bg: '#0c1a12', accent: '#4cc38a' },
};

declare global {
  interface Window {
    /** Called by the export header button: `onclick="__yeaboiCycleTheme()"`. */
    __yeaboiCycleTheme?: () => void;
  }
}

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

/** Read the persisted palette for a surface, or `null` when unset or unknown. */
export function storedTheme(key: string = THEME_KEYS.export): Theme | null {
  const raw = read('local', key);
  return isTheme(raw) ? raw : null;
}

/** Apply and persist a palette. */
export function setTheme(theme: Theme, key: string = THEME_KEYS.export): void {
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
export function applyStoredTheme(key: string = THEME_KEYS.export): Theme {
  const theme = storedTheme(key) ?? preferred();
  applyTheme(theme);
  return theme;
}

/** The next palette in cycle order. */
export function nextTheme(theme: Theme): Theme {
  return THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length] as Theme;
}

/**
 * Apply the starting palette and wire up the export pages' cycle button.
 *
 * Only the static exports use this: they have no React runtime, just a header
 * button with an inline `onclick`. The live boards use the `<ThemeSwitcher>`
 * component instead, which offers all five at once rather than cycling.
 */
export function installThemeSwitcher(key: string = THEME_KEYS.export): void {
  let theme: Theme = applyStoredTheme(key);
  window.__yeaboiCycleTheme = function cycleTheme(): void {
    theme = nextTheme(theme);
    setTheme(theme, key);
  };
}
