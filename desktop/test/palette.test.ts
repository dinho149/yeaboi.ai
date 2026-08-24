// Cmd+K's matching and the shortcuts sheet's copy.

import { describe, expect, it } from 'vitest';
import {
  type PaletteEntry,
  isTyping,
  matchEntries,
  moveSelection,
  paletteEntries,
  shortcuts,
} from '../src/renderer/palette';

const ENTRIES: PaletteEntry[] = [
  { path: '/humans/planning/sessions', title: 'Saved plans', group: 'Humans' },
  { path: '/humans/standup', title: 'Standup', group: 'Humans' },
  { path: '/settings/sharing', title: 'Sharing', group: 'Settings' },
  { path: '/humans/analysis', title: 'Team analysis', group: 'Humans' },
  { path: '/usage', title: 'Analytics', group: '' },
];

describe('paletteEntries', () => {
  it('skips the actions and dialogs — they are buttons, not destinations', () => {
    const paths = paletteEntries([
      { path: '/home', capability: null, title: 'Home' },
      { path: 'dialog:share', capability: 'output-sharing', title: 'Share online' },
      { path: 'action:anonymize', capability: 'anonymize', title: 'Anonymize output' },
    ]).map((entry) => entry.path);
    expect(paths).toEqual(['/home']);
  });

  it('labels a route with the sidebar group it sits under', () => {
    const entries = paletteEntries([
      { path: '/agents/usage', capability: 'agent-usage', title: 'Usage' },
      { path: '/provenance', capability: 'provenance', title: 'Provenance' },
      { path: '/usage', capability: 'usage', title: 'Spend' },
    ]);
    expect(entries.map((entry) => entry.group)).toEqual(['Agents', 'Ops', '']);
  });
});

describe('matchEntries', () => {
  it('shows everything until something is typed', () => {
    expect(matchEntries(ENTRIES, '  ')).toHaveLength(ENTRIES.length);
  });

  it('prefers a title that starts with the query over one that merely contains it', () => {
    // Without the rank, "Team analysis" would come first — it is earlier in
    // no ordering that matters, but registry order alone would decide.
    expect(matchEntries(ENTRIES, 'an').map((e) => e.title)).toEqual([
      'Analytics',
      'Saved plans',
      'Standup',
      'Team analysis',
    ]);
  });

  it('keeps the registry order among equals — it is the sidebar order', () => {
    const starters = matchEntries(ENTRIES, 's')
      .map((entry) => entry.title)
      .slice(0, 3);
    expect(starters).toEqual(['Saved plans', 'Standup', 'Sharing']);
  });

  it('falls back to the path when no title matches', () => {
    expect(matchEntries(ENTRIES, '/settings').map((e) => e.path)).toEqual(['/settings/sharing']);
  });

  it('ignores case', () => {
    expect(matchEntries(ENTRIES, 'STANDUP')).toHaveLength(1);
  });

  it('returns nothing rather than everything when nothing matches', () => {
    expect(matchEntries(ENTRIES, 'zzz')).toEqual([]);
  });
});

describe('moveSelection', () => {
  it('stops at both ends rather than wrapping', () => {
    expect(moveSelection(0, -1, 4)).toBe(0);
    expect(moveSelection(3, 1, 4)).toBe(3);
    expect(moveSelection(1, 1, 4)).toBe(2);
  });

  it('is safe on an empty list', () => {
    expect(moveSelection(3, 1, 0)).toBe(0);
  });
});

describe('isTyping', () => {
  it('says yes for the fields a bare "?" must reach', () => {
    expect(isTyping({ tagName: 'TEXTAREA' } as unknown as EventTarget)).toBe(true);
    expect(isTyping({ tagName: 'INPUT' } as unknown as EventTarget)).toBe(true);
    expect(isTyping({ tagName: 'DIV', isContentEditable: true } as unknown as EventTarget)).toBe(true);
  });

  it('says no for the page itself', () => {
    expect(isTyping({ tagName: 'BODY' } as unknown as EventTarget)).toBe(false);
    expect(isTyping(null)).toBe(false);
  });
});

describe('shortcuts', () => {
  it('names the modifier the way the platform does', () => {
    expect(shortcuts('darwin')[0]?.keys).toBe('Cmd+K');
    expect(shortcuts('win32')[0]?.keys).toBe('Ctrl+K');
  });

  it('never advertises a gesture a window cannot make', () => {
    // Double-tap Space and Esc Esc exist because of the terminal; both are
    // gone here, and offering them would be a lie.
    const all = shortcuts('darwin')
      .map((row) => `${row.keys} ${row.what}`)
      .join(' ')
      .toLowerCase();
    expect(all).not.toContain('double-tap');
    expect(all).not.toContain('esc esc');
  });
});
