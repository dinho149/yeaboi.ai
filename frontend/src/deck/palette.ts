/**
 * Painting a reporting palette onto the design tokens.
 *
 * The deck has its own palette system — four built-ins plus whatever the user
 * put in `reporting_themes.json` — which is older than the design tokens and
 * outlives them, because the same palettes drive the `.pptx` export where there
 * is no CSS at all. So rather than fork the token layer, a deck palette is
 * *mapped onto* it: six roles become six custom properties on `<html>`, and
 * every component in the bundle (including the shared primitives) follows.
 *
 * Inline custom properties rather than a generated `<style>` block, because a
 * stylesheet is what this replaced — the old renderer concatenated a
 * `[data-theme="…"] { … }` rule per custom palette in Python. Inline properties
 * also win the cascade outright, so nothing has to reason about specificity
 * against the five site themes.
 *
 * The attribute is `data-deck-theme`, deliberately *not* `data-theme`. Those
 * would collide: a deck palette named `midnight` and a site palette named
 * `midnight` are unrelated, and a user is free to name a custom palette
 * `forest`, at which point the site block would start leaking into the tokens
 * this file does not override.
 */

import type { DeckPalette } from './boot';

/** The role → token mapping. `--deck-glow` is the deck's own; the rest are shared. */
const TOKENS: Array<[keyof DeckPalette, string]> = [
  ['bg1', '--bg'],
  ['bg2', '--deck-glow'],
  ['fg', '--text'],
  ['muted', '--muted'],
  ['accent', '--accent'],
  ['accent2', '--accent2'],
];

/** Apply `palette` to the document and record which one it is. */
export function applyPalette(name: string, palette: DeckPalette): void {
  const root = document.documentElement;
  root.setAttribute('data-deck-theme', name);
  for (const [role, token] of TOKENS) root.style.setProperty(token, palette[role]);
}

/** The next palette name in cycle order — built-ins first, then customs. */
export function nextTheme(names: string[], current: string): string {
  if (!names.length) return current;
  const at = names.indexOf(current);
  return names[(at + 1) % names.length] as string;
}

const HEX = /^#[0-9a-f]{6}$/i;

/**
 * Resolve a style colour against a palette.
 *
 * Port of `reporting.style.resolve_color`, and it has to stay one: the `.pptx`
 * renderer resolves the same two style fields with that function, so a deck and
 * the PowerPoint built from the same report must agree about what
 * `heading_color: "accent2"` means.
 *
 * Returns `undefined` rather than a default so the caller can simply omit the
 * property and let the stylesheet's own value stand.
 */
export function resolveColor(value: string, palette: DeckPalette): string | undefined {
  if (!value) return undefined;
  if (value in palette) return palette[value as keyof DeckPalette];
  if (HEX.test(value)) return value.toLowerCase();
  return undefined;
}
