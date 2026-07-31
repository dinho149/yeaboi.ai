/**
 * The deck's palettes, measured — against every surface they can now land on.
 *
 * This audit used to check a deck palette against *itself*: the palette owned
 * the background as well as the accents, so `--accent` on `--bg` was two
 * numbers from the same four-hex block, and `--panel` and `--dim` were mixed
 * from them in `deck.css`.
 *
 * A deck palette contributes an accent now. The surface underneath is whichever
 * of the five site palettes the visitor chose — which is what finally gives the
 * deck a light mode, and which is also a much sharper risk: every palette
 * Python ships was tuned against a dark background, and `sunset`'s orange on
 * the `light` theme's near-white is 2.1:1 unaided.
 *
 * So this measures the real combination — four deck palettes × five site
 * backgrounds — and it measures what `applyPalette` actually writes, not the
 * raw hex, because `readable()` darkens an accent until it clears AA. A failure
 * here means that derivation stopped working, which is the only thing standing
 * between a custom palette and an unreadable slide.
 *
 * The site palettes' own tokens (`--panel`, `--dim`, `--muted`) are audited in
 * `runtime/theme.test.ts` and are no longer the deck's business.
 *
 * What this cannot cover: whether a user's own four hexes in
 * `reporting_themes.json` were a good idea. It can only promise they end up
 * legible, which is what `readable()` is for.
 */

import { describe, expect, it } from 'vitest';

import { AA_TEXT, contrast, parsePalettes } from '../design/contrast';
import paletteCss from '../design/palette.css?raw';
import { DECK_WIRE } from '../test/fixtures/wire';
import { readableAccent } from './palette';

const DECK_PALETTES = DECK_WIRE.palettes;
const SITE_PALETTES = parsePalettes(paletteCss);

const DECK_NAMES = Object.keys(DECK_PALETTES);
const SITE_NAMES = Object.keys(SITE_PALETTES);
const ACCENTS = ['accent', 'accent2'] as const;

describe('the built-in deck palettes', () => {
  it('are the ones Python ships', () => {
    // Straight from the payload the exporter writes, so a palette added in
    // themes.py is audited without anyone remembering to add it here.
    expect(DECK_NAMES).toEqual(['aurora', 'midnight', 'mono', 'sunset']);
  });

  it('are audited against every site palette, not just a dark one', () => {
    // A regex that matched nothing would let every case below pass vacuously.
    expect(SITE_NAMES).toEqual(['midnight', 'light', 'solarized', 'synthwave', 'forest']);
  });
});

describe('a deck accent on a site background', () => {
  const cases = DECK_NAMES.flatMap((deck) =>
    SITE_NAMES.flatMap((site) => ACCENTS.map((role) => [deck, site, role] as const)),
  );

  it.each(cases)('%s/%s: --%s clears AA once applied', (deck, site, role) => {
    const bg = SITE_PALETTES[site]?.['bg'] as string;
    const raw = DECK_PALETTES[deck]![role];
    const applied = readableAccent(raw, bg);
    const ratio = contrast(applied, bg);
    expect(ratio, `${deck} ${role} on ${site} --bg is ${ratio.toFixed(2)}:1 (raw ${raw} → ${applied})`).toBeGreaterThanOrEqual(
      AA_TEXT,
    );
  });

  it('leaves an accent alone when it already reads', () => {
    // The derivation must be a floor, not a filter: a palette tuned for a dark
    // projector should look exactly as its author chose on a dark theme.
    const bg = SITE_PALETTES['midnight']?.['bg'] as string;
    const raw = DECK_PALETTES['aurora']!.accent;
    expect(contrast(raw, bg)).toBeGreaterThanOrEqual(AA_TEXT);
    expect(readableAccent(raw, bg)).toBe(raw);
  });

  it('actually changes the ones that do not', () => {
    // The case that motivated this: sunset's orange on the light theme.
    const bg = SITE_PALETTES['light']?.['bg'] as string;
    const raw = DECK_PALETTES['sunset']!.accent;
    expect(contrast(raw, bg), 'sunset accent was already legible on light — pick a different case').toBeLessThan(
      AA_TEXT,
    );
    expect(readableAccent(raw, bg)).not.toBe(raw);
  });

  it('returns an unparseable colour untouched rather than throwing', () => {
    // A palette is user data. A deck that renders someone's odd colour beats a
    // deck that does not render.
    expect(readableAccent('not-a-colour', '#0b0c0e')).toBe('not-a-colour');
  });
});
