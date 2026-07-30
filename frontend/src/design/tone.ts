/**
 * `Tone` — the semantic colour vocabulary, as a closed type.
 *
 * ## What this replaces
 *
 * On the Python side, chart helpers take a CSS custom-property *name* as a
 * string and interpolate it into a `style` attribute, so `html_theme` needs
 * `_safe_css_var()` to check at runtime that it looks like `--token` before
 * letting it through. That is a whitelist enforced by a regex, checked on every
 * call, with a silent fallback when it fails.
 *
 * Here the whitelist **is the type**. `tone="ok"` compiles; `tone="; url(evil)"`
 * does not. There is nothing to sanitise because there is nothing to inject —
 * the mapping from tone to custom property happens in this file and nowhere
 * else, and `_safe_css_var` has no counterpart to port.
 *
 * When phase 9 moves the exporters to TSX, `_safe_css_var` deletes outright.
 */

export const TONES = [
  'accent',
  'accent2',
  'ok',
  'warn',
  'danger',
  'info',
  'critical',
  'high',
  'medium',
  'low',
  'muted',
] as const;

export type Tone = (typeof TONES)[number];

/** The CSS custom property backing a tone, ready to drop into `var()`. */
export function toneVar(tone: Tone): string {
  return `var(--${tone})`;
}

/**
 * A tone mixed into transparency, for fills and borders.
 *
 * `color-mix` rather than an `rgba()` literal, because the underlying value is
 * a custom property whose channels this code never sees — and because a fill
 * derived from the token automatically follows a theme change.
 */
export function toneMix(tone: Tone, percent: number, into = 'transparent'): string {
  return `color-mix(in srgb, ${toneVar(tone)} ${percent}%, ${into})`;
}

/**
 * The rotation used for uncoloured breakdowns (chart series, avatar colours).
 *
 * Fixed order so the same data draws the same way twice, and short so an
 * overflow bucket folds into `muted` instead of the palette inventing hues that
 * no theme defines.
 */
export const SERIES_TONES: readonly Tone[] = ['accent', 'accent2', 'info', 'ok', 'warn', 'high', 'medium'];

/** The subset used for deterministic avatar colours. Mirrors `html_theme._AVATAR_VARS`. */
export const AVATAR_TONES: readonly Tone[] = ['accent', 'accent2', 'info', 'ok', 'warn', 'high'];
