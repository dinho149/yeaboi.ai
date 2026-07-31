/**
 * Type for the `toHaveNoViolations` matcher.
 *
 * `vitest-axe` ships its own augmentation, but it targets the global `Vi`
 * namespace — the vitest 0.x/1.x convention. Vitest 4 declares matchers by
 * augmenting the `vitest` module instead, so the packaged types are inert here
 * and `tsc` reports the matcher as missing while the tests pass at runtime.
 *
 * Registering the matcher itself still happens in setup.ts; this is only the
 * declaration that makes `tsc` agree it exists.
 */

import 'vitest';

declare module 'vitest' {
  interface Matchers<T = unknown> {
    /** Assert an axe run produced no accessibility violations. */
    toHaveNoViolations(): T;
  }
}
