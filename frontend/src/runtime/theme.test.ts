/**
 * The theme audit: every palette, every foreground, measured.
 *
 * This is the test the design system exists to make possible. Contrast was
 * never checked before — the palettes were hand-copied into three files and
 * eyeballed — and the measurement found real failures: Solarized rendered
 * `--danger` at 2.81:1 and `--accent2` at 2.86:1 against `--panel`, below even
 * the 3:1 floor for non-text UI, and Forest's `--danger` sat at 4.30:1. Those
 * are fixed in palette.css; this keeps them fixed.
 */

import { describe, expect, it } from 'vitest';

// `?raw` rather than node:fs — the test config inherits the real build config
// (that is what makes the preact aliases match the shipped bundle), and that
// config targets a browser, where node builtins are externalized to a stub.
import paletteCss from '../design/palette.css?raw';
import { AA_TEXT, contrast, parsePalettes } from '../design/contrast';
import { isTheme, nextTheme, THEME_PREVIEW, THEMES, type Theme } from './theme';

const PALETTES = parsePalettes(paletteCss);

/** Every token used as text or as a meaningful graphic on a surface. */
const FOREGROUNDS = [
  'text',
  'muted',
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
] as const;

/** Every token a foreground is painted on. */
const SURFACES = ['bg', 'panel', 'card'] as const;

describe('palette.css', () => {
  it('defines exactly the themes the runtime cycles through', () => {
    // Two-way: a palette nobody can select, or a selectable theme with no
    // palette (which renders as unstyled midnight), both fail here.
    expect(Object.keys(PALETTES).sort()).toEqual([...THEMES].sort());
  });

  it.each(THEMES)('%s defines every token the components reference', (theme) => {
    const tokens = PALETTES[theme] ?? {};
    for (const token of [...FOREGROUNDS, ...SURFACES, 'ink', 'line']) {
      expect(tokens[token], `${theme} is missing --${token}`).toBeTruthy();
    }
  });
});

describe('WCAG contrast', () => {
  // One case per (theme, foreground, surface) so a failure names the exact
  // pair rather than "the contrast test failed".
  const cases = THEMES.flatMap((theme) =>
    FOREGROUNDS.flatMap((fg) => SURFACES.map((bg) => [theme, fg, bg] as const))
  );

  it.each(cases)('%s: --%s on --%s clears AA', (theme, fg, bg) => {
    const tokens = PALETTES[theme] as Record<string, string>;
    const ratio = contrast(tokens[fg] as string, tokens[bg] as string);
    expect(ratio, `${theme}: --${fg} on --${bg} is ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(AA_TEXT);
  });

  it.each(THEMES)('%s: --ink is readable on a filled accent button', (theme) => {
    // The primary button is `background: var(--accent); color: var(--ink)`.
    // Nothing else in the palette pairs those two, so it needs its own case.
    const tokens = PALETTES[theme] as Record<string, string>;
    const ratio = contrast(tokens['ink'] as string, tokens['accent'] as string);
    expect(ratio, `${theme}: --ink on --accent is ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(AA_TEXT);
  });
});

describe('THEME_PREVIEW', () => {
  // The swatch colours are a hand-written copy of two values per theme, kept
  // static so the picker no longer forces a layout per swatch to read them
  // back out of a throwaway DOM node. This is what stops the copy drifting.
  it.each(THEMES)('%s matches palette.css', (theme) => {
    const tokens = PALETTES[theme] as Record<string, string>;
    expect(THEME_PREVIEW[theme].bg.toLowerCase()).toBe((tokens['bg'] as string).toLowerCase());
    expect(THEME_PREVIEW[theme].accent.toLowerCase()).toBe((tokens['accent'] as string).toLowerCase());
  });
});

describe('theme helpers', () => {
  it('accepts only known theme names', () => {
    expect(isTheme('midnight')).toBe(true);
    expect(isTheme('MIDNIGHT')).toBe(false);
    expect(isTheme('')).toBe(false);
    expect(isTheme(null)).toBe(false);
  });

  it('cycles through every theme and returns to the start', () => {
    let theme: Theme = THEMES[0];
    const seen: Theme[] = [theme];
    for (let i = 0; i < THEMES.length - 1; i += 1) {
      theme = nextTheme(theme);
      seen.push(theme);
    }
    expect(seen).toEqual([...THEMES]);
    expect(nextTheme(theme)).toBe(THEMES[0]);
  });
});
