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

import { beforeEach, describe, expect, it } from 'vitest';

// `?raw` rather than node:fs — the test config inherits the real build config
// (that is what makes the preact aliases match the shipped bundle), and that
// config targets a browser, where node builtins are externalized to a stub.
import paletteCss from '../design/palette.css?raw';
import tokensCss from '../design/tokens.css?raw';
import {
  AA_NON_TEXT,
  AA_TEXT,
  contrast,
  effectiveAccent,
  parseModeAccents,
  parsePalettes,
} from '../design/contrast';
import {
  isTheme,
  nextTheme,
  setTheme,
  storedTheme,
  THEME_KEYS,
  THEME_PREVIEW,
  THEMES,
  type Theme,
} from './theme';

const PALETTES = parsePalettes(paletteCss);
const MODE_ACCENTS = parseModeAccents(tokensCss);

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
    for (const token of [...FOREGROUNDS, ...SURFACES, 'ink', 'line', 'dim']) {
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

  it.each(THEMES)('%s: --dim clears the non-text floor but is never body text', (theme) => {
    // --dim is the third text tier (yeaboi.ai's `--text-dim`), for furniture
    // that must recede: a disabled hint, an inactive eyebrow, the dot pager.
    // It is deliberately BELOW the 4.5:1 body-text floor, so auditing it with
    // FOREGROUNDS would either fail or force it up until it stopped receding.
    // 3:1 is the right bar, and pinning it here stops anyone quietly using it
    // for a sentence.
    const tokens = PALETTES[theme] as Record<string, string>;
    const ratio = contrast(tokens['dim'] as string, tokens['bg'] as string);
    expect(ratio, `${theme}: --dim on --bg is ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(AA_NON_TEXT);
    expect(ratio, `${theme}: --dim on --bg is ${ratio.toFixed(2)}:1 — that is body text, not dim`).toBeLessThan(
      AA_TEXT
    );
  });

  it.each(THEMES)('%s: --ink is readable on a filled accent button', (theme) => {
    // The primary button is `background: var(--accent); color: var(--ink)`.
    // Nothing else in the palette pairs those two, so it needs its own case.
    const tokens = PALETTES[theme] as Record<string, string>;
    const ratio = contrast(tokens['ink'] as string, tokens['accent'] as string);
    expect(ratio, `${theme}: --ink on --accent is ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(AA_TEXT);
  });
});

describe('mode accents', () => {
  // Every mode the TUI defines has to work on every theme, because the visitor
  // picks the theme and the host picks the mode. This is the cross-product the
  // palette audit above structurally cannot reach — and when it was first run
  // it found retro's teal at 2.09:1 on the light theme, on a shipping board.
  const modes = Object.keys(MODE_ACCENTS.base);

  it('covers every mode the TUI themes', () => {
    // Two-way against ui/shared/_components.py. A mode added there with no
    // accent here silently inherits the theme's, which is how poker ended up
    // green while its TUI theme was gold.
    expect(modes.slice().sort()).toEqual(
      ['analysis', 'performance', 'planning', 'poker', 'reporting', 'retro', 'ship', 'standup', 'usage'].sort()
    );
  });

  it('gives every mode a light-surface rendition', () => {
    // Not optional: a mode with no light override falls back to its terminal
    // hue and gets painted on white.
    expect(Object.keys(MODE_ACCENTS.light).slice().sort()).toEqual(modes.slice().sort());
  });

  const cases = modes.flatMap((mode) =>
    THEMES.flatMap((theme) => (['bg', 'panel', 'card'] as const).map((bg) => [mode, theme, bg] as const))
  );

  it.each(cases)('%s accent on %s --%s clears AA', (mode, theme, bg) => {
    const tokens = PALETTES[theme] as Record<string, string>;
    const accent = effectiveAccent(MODE_ACCENTS, mode, theme);
    const ratio = contrast(accent, tokens[bg] as string);
    expect(ratio, `${mode} on ${theme} --${bg} is ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(AA_TEXT);
  });

  it.each(modes.flatMap((mode) => THEMES.map((theme) => [mode, theme] as const)))(
    '%s accent on %s takes --ink legibly',
    (mode, theme) => {
      // The primary button is `background: var(--accent); color: var(--ink)`,
      // and --ink comes from the theme while --accent comes from the mode, so
      // neither block on its own can be checked for this.
      const tokens = PALETTES[theme] as Record<string, string>;
      const accent = effectiveAccent(MODE_ACCENTS, mode, theme);
      const ratio = contrast(tokens['ink'] as string, accent);
      expect(ratio, `${mode}/${theme}: --ink on the accent is ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(
        AA_TEXT
      );
    }
  );
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

describe('one theme key', () => {
  beforeEach(() => localStorage.clear());

  it('every surface reads and writes the same key', () => {
    setTheme('forest');
    expect(localStorage.getItem('yeaboi-export-theme')).toBe('forest');
    expect(storedTheme()).toBe('forest');
  });

  it('keeps the literal string the exports on disk already read', () => {
    // A report written last month carries a bundle that reads this exact key
    // and nothing else. Renaming the constant is free; renaming the string is
    // not, and three Python tests grep it out of rendered HTML.
    expect(THEME_KEYS.site).toBe('yeaboi-export-theme');
    expect(THEME_KEYS.export).toBe(THEME_KEYS.site);
  });

  it('adopts a legacy board key once, then writes it forward', () => {
    // Without this, shipping the collapse would reset the palette of every
    // board mid-ceremony.
    localStorage.setItem('retro_theme', 'synthwave');
    expect(storedTheme()).toBe('synthwave');
    expect(localStorage.getItem('yeaboi-export-theme')).toBe('synthwave');
  });

  it('prefers the canonical key over a stale legacy one', () => {
    localStorage.setItem('retro_theme', 'synthwave');
    localStorage.setItem('yeaboi-export-theme', 'forest');
    expect(storedTheme()).toBe('forest');
  });

  it('ignores a legacy key holding something that is not a theme', () => {
    localStorage.setItem('poker_theme', 'chartreuse');
    expect(storedTheme()).toBeNull();
  });
});
