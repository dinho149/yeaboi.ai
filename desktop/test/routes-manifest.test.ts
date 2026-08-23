// The desktop half of the parity seam: the committed Python-side manifest must
// equal what routes.json generates (the Python suite reads the manifest and
// never runs Node — this test and `npm run check-manifest` are what keep the
// two sides in step).

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import registry from '../src/renderer/routes.json';
import { APP_ROUTES, DEFAULT_ROUTE, routeFor } from '../src/renderer/routes';

const MANIFEST = resolve(import.meta.dirname, '../../src/yeaboi/app/routes_manifest.json');

describe('routes manifest', () => {
  it('committed manifest equals the routes.json registry', () => {
    const committed = JSON.parse(readFileSync(MANIFEST, 'utf-8'));
    expect(committed).toEqual(registry);
  });

  it('every route is well-formed', () => {
    for (const route of APP_ROUTES) {
      expect(route.path).toMatch(/^(\/|action:|dialog:)/);
      expect(route.title.length).toBeGreaterThan(0);
    }
  });

  it('paths are unique', () => {
    const paths = APP_ROUTES.map((r) => r.path);
    expect(new Set(paths).size).toBe(paths.length);
  });

  it('the default route exists', () => {
    expect(routeFor(DEFAULT_ROUTE)).toBeDefined();
  });
});
