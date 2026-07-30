/**
 * The deck's palettes, measured — including the tiers CSS derives.
 *
 * The site's five palettes are audited in `runtime/theme.test.ts`. The deck's
 * four are a separate, older set (`reporting/themes.py`) that also drive the
 * `.pptx`, and they were never checked: they predate the design system and were
 * hand-copied between a Python dict and a CSS block for as long as both existed.
 *
 * Two of the tokens they resolve to are not in the palette at all — `--panel`
 * and `--dim` are mixed in `deck.css` from `--text` / `--muted` and `--bg`, so
 * a value nobody wrote is what a metric tile's border and the keyboard hint
 * actually paint with. Those are the ones worth measuring, and they are exactly
 * the ones a stylesheet makes easy to stop measuring.
 *
 * The mix ratios are parsed out of `deck.css` rather than repeated here, so
 * editing the stylesheet re-runs the audit against the new number instead of
 * silently invalidating it.
 *
 * What this cannot cover: a palette a user wrote into `reporting_themes.json`.
 * Four hexes of their choosing can be any contrast at all, and refusing to
 * render one would be worse than showing it.
 */

import { describe, expect, it } from 'vitest';

import deckCss from './deck.css?raw';
import { AA_NON_TEXT, AA_TEXT, contrast, mixSrgb } from '../design/contrast';
import { DECK_WIRE } from '../test/fixtures/wire';

/** `--name: color-mix(in srgb, var(--role) NN%, var(--base))` → the three parts. */
function derivations(css: string): Record<string, { role: string; weight: number; base: string }> {
  const out: Record<string, { role: string; weight: number; base: string }> = {};
  const re = /--([\w-]+):\s*color-mix\(in srgb,\s*var\(--([\w-]+)\)\s*([\d.]+)%,\s*var\(--([\w-]+)\)\)/g;
  for (let m = re.exec(css); m !== null; m = re.exec(css)) {
    out[m[1] as string] = { role: m[2] as string, weight: Number(m[3]) / 100, base: m[4] as string };
  }
  return out;
}

const DERIVED = derivations(deckCss);
const PALETTES = DECK_WIRE.palettes;

/** A deck palette's six roles, under the token names the components use. */
function tokens(name: string): Record<string, string> {
  const p = PALETTES[name]!;
  const base: Record<string, string> = {
    bg: p.bg1,
    text: p.fg,
    muted: p.muted,
    accent: p.accent,
    accent2: p.accent2,
  };
  for (const [token, { role, weight, base: onto }] of Object.entries(DERIVED)) {
    base[token] = mixSrgb(base[role] as string, base[onto] as string, weight);
  }
  return base;
}

const NAMES = Object.keys(PALETTES);
const FOREGROUNDS = ['text', 'muted', 'accent', 'accent2'] as const;
const SURFACES = ['bg', 'panel'] as const;

describe('deck.css derivations', () => {
  it('parsed the mixes it is about to audit', () => {
    // A regex that matched nothing would let every case below pass vacuously.
    expect(Object.keys(DERIVED).sort()).toEqual(['dim', 'panel']);
    expect(DERIVED['panel']).toEqual({ role: 'text', weight: 0.04, base: 'bg' });
  });
});

describe('the four built-in deck palettes', () => {
  it('are the ones Python ships', () => {
    // Straight from the payload the exporter writes, so a palette added in
    // themes.py is audited without anyone remembering to add it here.
    expect(NAMES).toEqual(['aurora', 'midnight', 'mono', 'sunset']);
  });

  const cases = NAMES.flatMap((name) => FOREGROUNDS.flatMap((fg) => SURFACES.map((bg) => [name, fg, bg] as const)));

  it.each(cases)('%s: --%s on --%s clears AA', (name, fg, bg) => {
    const t = tokens(name);
    const ratio = contrast(t[fg] as string, t[bg] as string);
    expect(ratio, `${name}: --${fg} on --${bg} is ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(AA_TEXT);
  });

  it.each(NAMES)('%s: --dim recedes without disappearing', (name) => {
    // Same band the site palettes hold `--dim` in, and for the same reason:
    // above the non-text floor so the rail and the keyboard hint are legible,
    // deliberately below body text so nobody sets a sentence in it.
    const t = tokens(name);
    const ratio = contrast(t['dim'] as string, t['bg'] as string);
    expect(ratio, `${name}: --dim on --bg is ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(AA_NON_TEXT);
    expect(ratio, `${name}: --dim on --bg is ${ratio.toFixed(2)}:1 — that is body text, not dim`).toBeLessThan(AA_TEXT);
  });
});
