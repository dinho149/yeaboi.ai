/**
 * Route naming and the announcement it drives.
 *
 * A client-side router silently drops three things a page load gives for free:
 * the title, an announcement, and focus. These pin the first two; the focus
 * move is asserted in App's own tests via the ref.
 */

import { describe, expect, it } from 'vitest';

import { APP_NAME, documentTitle, pageName } from './routeTitle';

describe('pageName', () => {
  it.each([
    ['/', 'Projects'],
    ['/projects', 'Projects'],
    ['/projects/{id}', 'Project'],
    ['/projects/{id}/artifacts/{artifactId}', 'Document'],
    ['/settings', 'Settings'],
  ])('names %s', (pattern, expected) => {
    expect(pageName(pattern, '/whatever')).toBe(expected);
  });

  it('names an unmatched path, and says which one', () => {
    // "Not found" alone tells a screen-reader user nothing about where they are.
    expect(pageName(null, '/nope')).toBe('Not found: /nope');
  });
});

describe('documentTitle', () => {
  it('puts the page first, because a tab strip truncates from the right', () => {
    expect(documentTitle('/settings', '/settings')).toBe(`Settings · ${APP_NAME}`);
  });

  it('always carries the product name', () => {
    for (const pattern of ['/', '/settings', null]) {
      expect(documentTitle(pattern, '/x')).toContain(APP_NAME);
    }
  });
});
