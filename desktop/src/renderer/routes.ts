// The desktop route registry — the renderer half of the surface-parity seam.
//
// routes.json is the single source: this module types it for the router, and
// scripts/gen-routes-manifest.mjs code-generates it into
// src/yeaboi/app/routes_manifest.json (committed), which
// tests/unit/test_surface_parity.py::TestDesktop checks two-way against the
// CAPABILITIES desktop column. Add a page here → regenerate the manifest →
// register the capability, or the build fails on one side or the other.

import registry from './routes.json';

export interface AppRoute {
  path: string;
  /** The CAPABILITIES key this page implements, or null for pure chrome. */
  capability: string | null;
  title: string;
}

export const APP_ROUTES: readonly AppRoute[] = registry.routes;

export const DEFAULT_ROUTE = '/home';

export function routeFor(path: string): AppRoute | undefined {
  return APP_ROUTES.find((route) => route.path === path);
}
