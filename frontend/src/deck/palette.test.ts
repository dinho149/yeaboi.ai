/**
 * The palette layer, and the one cross-language contract in it.
 *
 * `resolveColor` has a twin in Python (`reporting.style.resolve_color`) that the
 * `.pptx` renderer uses on the same two style fields. A deck and the PowerPoint
 * built from the same report have to agree about what `heading_color: "accent2"`
 * means, so the cases below are the Python function's branches, one for one.
 */

import { beforeEach, describe, expect, it } from 'vitest';

import { DECK_WIRE } from '../test/fixtures/wire';
import { applyPalette, nextTheme, resolveColor } from './palette';

const AURORA = DECK_WIRE.palettes['aurora']!;

describe('resolveColor', () => {
  it('treats the empty string as "no override"', () => {
    // undefined rather than a default, so the caller omits the property and the
    // stylesheet's own value stands. A default here would silently become the
    // colour of every unstyled deck.
    expect(resolveColor('', AURORA)).toBeUndefined();
  });

  it('looks a role up in the active palette', () => {
    expect(resolveColor('accent2', AURORA)).toBe('#6ff0d0');
    expect(resolveColor('muted', AURORA)).toBe('#8fc9be');
  });

  it('passes a hex through, lowercased', () => {
    expect(resolveColor('#FF0000', AURORA)).toBe('#ff0000');
  });

  it('ignores anything that is neither', () => {
    expect(resolveColor('rebeccapurple', AURORA)).toBeUndefined();
    expect(resolveColor('#f00', AURORA)).toBeUndefined();
    expect(resolveColor('url(evil)', AURORA)).toBeUndefined();
  });
});

describe('applyPalette', () => {
  beforeEach(() => {
    document.documentElement.removeAttribute('style');
    document.documentElement.removeAttribute('data-deck-theme');
  });

  it('maps the six roles onto the design tokens', () => {
    applyPalette('aurora', AURORA);
    const root = document.documentElement;
    expect(root.style.getPropertyValue('--bg')).toBe(AURORA.bg1);
    expect(root.style.getPropertyValue('--deck-glow')).toBe(AURORA.bg2);
    expect(root.style.getPropertyValue('--text')).toBe(AURORA.fg);
    expect(root.style.getPropertyValue('--muted')).toBe(AURORA.muted);
    expect(root.style.getPropertyValue('--accent')).toBe(AURORA.accent);
    expect(root.style.getPropertyValue('--accent2')).toBe(AURORA.accent2);
  });

  it('records the palette under its own attribute, not data-theme', () => {
    // `data-theme` already means one of the five *site* palettes. A deck theme
    // named "midnight" is a different thing, and a user is free to name a
    // custom one "forest" — at which point a site block would start leaking
    // into the tokens this file does not override.
    applyPalette('sunset', DECK_WIRE.palettes['sunset']!);
    expect(document.documentElement.getAttribute('data-deck-theme')).toBe('sunset');
    expect(document.documentElement.hasAttribute('data-theme')).toBe(false);
  });
});

describe('nextTheme', () => {
  const names = ['midnight', 'aurora', 'sunset', 'mono'];

  it('advances and wraps', () => {
    expect(nextTheme(names, 'midnight')).toBe('aurora');
    expect(nextTheme(names, 'mono')).toBe('midnight');
  });

  it('starts from the front when the current name is unknown', () => {
    // A deck that outlived the palette it was exported with still cycles.
    expect(nextTheme(names, 'deleted-custom')).toBe('midnight');
  });

  it('is a no-op with nothing to cycle', () => {
    expect(nextTheme([], 'midnight')).toBe('midnight');
  });
});
