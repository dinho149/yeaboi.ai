// The desktop half of the parity seam: the committed Python-side manifest must
// equal what routes.json generates (the Python suite reads the manifest and
// never runs Node — this test and `npm run check-manifest` are what keep the
// two sides in step).

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import registry from '../src/renderer/routes.json';
import { APP_ROUTES, DEFAULT_ROUTE, routeFor } from '../src/renderer/routes';
import { SETTINGS_TABS } from '../src/renderer/settings-tabs';

const MANIFEST = resolve(import.meta.dirname, '../../contracts/v1/routes_manifest.json');

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

describe('settings tabs', () => {
  it('every /settings/* route is a tab, and every tab is a route', () => {
    const routePaths = APP_ROUTES.filter((r) => r.path.startsWith('/settings/')).map((r) => r.path);
    const tabRoutes = SETTINGS_TABS.map((t) => t.route);
    expect(new Set(tabRoutes)).toEqual(new Set(routePaths));
  });

  it('no section is claimed by two tabs', () => {
    const sections = SETTINGS_TABS.flatMap((t) => [...t.sections]);
    expect(new Set(sections).size).toBe(sections.length);
  });
});
