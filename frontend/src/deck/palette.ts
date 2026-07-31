/**
 * Painting a reporting palette onto the design tokens.
 *
 * The deck has its own palette system — four built-ins plus whatever the user
 * put in `reporting_themes.json` — which is older than the design tokens and
 * outlives them, because the same palettes drive the `.pptx` export where there
 * is no CSS at all.
 *
 * ## What a deck palette is allowed to be
 *
 * It contributes an **accent**, exactly like `[data-mode]` does for every other
 * surface. It does not own the background, the text colour or the muted tier —
 * those come from the visitor's chosen site theme, like everywhere else.
 *
 * It used to own all six. That is why the deck was the one yeaboi surface with
 * no light mode: every palette Python ships is dark, so choosing `light` on a
 * report and then opening its slides got you a dark deck with no way back. It
 * is also why `@media print` could never reach it — inline properties on
 * `<html>` outrank any stylesheet rule, so the print block's light palette was
 * being silently overruled on every page.
 *
 * So the mapping is three deck-*private* properties now. They collide with
 * nothing, they leave `--bg`/`--text`/`--muted` to the theme layer, and a
 * stylesheet can still beat them with `!important` — which is exactly what the
 * print block does.
 *
 * Inline properties rather than a generated `[data-theme]` block, still: a
 * custom palette is user data read out of `reporting_themes.json` at export
 * time, so there is no build step that could emit CSS for it, and
 * `test_theme_blocks_exist_only_in_palette_css` forbids trying.
 */

import { AA_TEXT, contrast, luminance, mixSrgb } from '../design/contrast';
import type { DeckPalette } from './boot';

/**
 * The role → property mapping.
 *
 * `bg1`, `fg` and `muted` are deliberately absent: they are the surface, and
 * the surface belongs to the site theme. They still travel in the payload
 * because the `.pptx` renderer reads all six from the same table.
 */
const TOKENS: Array<[keyof DeckPalette, string]> = [
  ['bg2', '--deck-glow'],
  ['accent', '--deck-accent'],
  ['accent2', '--deck-accent2'],
];

/** Read a resolved token off the document, e.g. the theme's current `--bg`. */
function resolvedToken(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/**
 * Push `hex` away from `bg` until it reads, keeping its hue.
 *
 * The four built-in palettes are all tuned against their own dark backgrounds,
 * and a deck palette no longer supplies the background — so an accent can land
 * on any of the five site surfaces. Two different failures follow from that,
 * and the direction has to be chosen per background rather than assumed:
 *
 * * On `light`, sunset's orange washes out (2.1:1) and must **darken**.
 * * On `solarized`, midnight's violet is 4.25:1 — just under AA — and must
 *   **lighten**. Darkening it there would make it worse, which is precisely
 *   the bug the first version of this function had.
 *
 * So it mixes toward white or black depending on which side of the background
 * it is already on. `tokens.css` solves the same problem for the eight
 * `[data-mode]` accents by hand — it can, there are eight and they are checked
 * in. A custom deck palette does not exist until export time.
 *
 * `mixSrgb` is the same blend the contrast audit measures, so a colour produced
 * here is measurable by the same tools that vetted the tokens. Twenty steps of
 * 5% is cheaper than being clever, and it gives up rather than looping if a
 * palette is pathological — a slightly-off accent beats no deck.
 *
 * Tolerant for the same reason: a palette is user data from
 * `reporting_themes.json`, and `parseHex` throws. A colour that cannot be
 * parsed is left exactly as the user wrote it.
 */
export function readableAccent(hex: string, bg: string): string {
  try {
    if (contrast(hex, bg) >= AA_TEXT) return hex;
    // Away from the background, not simply darker. `contrast` is symmetric, so
    // the comparison against white is what says which side we are on.
    const target = luminance(bg) > luminance('#808080') ? '#000000' : '#ffffff';
    for (let step = 1; step <= 20; step++) {
      const shifted = mixSrgb(hex, target, 1 - step * 0.05);
      if (contrast(shifted, bg) >= AA_TEXT) return shifted;
    }
  } catch {
    // Unparseable colour, or a `--bg` that has not resolved yet (jsdom).
  }
  return hex;
}

/** Apply `palette` to the document and record which one it is. */
export function applyPalette(name: string, palette: DeckPalette): void {
  const root = document.documentElement;
  root.setAttribute('data-deck-theme', name);
  // Whatever the site theme resolved to, which is what the accents have to
  // survive against. Read once rather than per role.
  const bg = resolvedToken('--bg');
  for (const [role, token] of TOKENS) {
    const value = palette[role];
    root.style.setProperty(token, token === '--deck-glow' ? value : readableAccent(value, bg));
  }
}

/** The next palette name in cycle order — built-ins first, then customs. */
export function nextTheme(names: string[], current: string): string {
  if (!names.length) return current;
  const at = names.indexOf(current);
  return names[(at + 1) % names.length] as string;
}

const HEX = /^#[0-9a-f]{6}$/i;

/**
 * The three roles the site theme owns, and the token that carries each.
 *
 * A style field naming one of these must resolve to the *token*, not to the
 * palette's hex: `heading_color: "fg"` means "the ordinary text colour", and
 * pinning midnight's `#e6edf3` would put near-white headings on a light page.
 */
const SURFACE_TOKENS: Partial<Record<keyof DeckPalette, string>> = {
  bg1: 'var(--bg)',
  fg: 'var(--text)',
  muted: 'var(--muted)',
};

/**
 * Resolve a style colour against a palette.
 *
 * A port of `reporting.style.resolve_color`, and it has to stay close to one:
 * the `.pptx` renderer resolves the same two style fields with that function,
 * so a deck and the PowerPoint built from the same report must agree about what
 * `heading_color: "accent2"` means.
 *
 * It is no longer *identical*, and the divergence is the point. Python resolves
 * for PowerPoint, which has no cascade — a shape's colour is a number in a
 * file, so every role has to become a hex. The browser has a cascade and a
 * theme, so the three roles the theme owns resolve to their tokens and follow
 * the visitor's palette. Both sides still agree on which role is being named,
 * which is the part a reader is relying on.
 *
 * Returns `undefined` rather than a default so the caller can simply omit the
 * property and let the stylesheet's own value stand.
 */
export function resolveColor(value: string, palette: DeckPalette): string | undefined {
  if (!value) return undefined;
  if (value in SURFACE_TOKENS) return SURFACE_TOKENS[value as keyof DeckPalette];
  if (value === 'accent') return 'var(--deck-accent, var(--accent))';
  if (value === 'accent2') return 'var(--deck-accent2, var(--accent2))';
  if (value in palette) return palette[value as keyof DeckPalette];
  if (HEX.test(value)) return value.toLowerCase();
  return undefined;
}
