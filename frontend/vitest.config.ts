import { defineConfig, mergeConfig } from 'vitest/config';

import viteConfig from './vite.config';

/**
 * Test config, layered on the real build config.
 *
 * Reusing `vite.config.ts` is not tidiness — it is what makes the tests
 * meaningful. The preact/compat aliases live there, so tests exercise the same
 * module graph the shipped bundle does. A separate alias list would let the two
 * drift, and the tests would be verifying a React that never ships.
 *
 * `make test` stays pytest-only and never runs this: the Python suite reads the
 * committed bundles and builds nothing. These run in `npm test` and in CI's
 * `web` job.
 */
export default mergeConfig(
  // `viteConfig` is a function of ({mode, command}); resolve it against the
  // build shape so the aliases are present.
  viteConfig({ mode: 'export', command: 'build' } as never),
  defineConfig({
    test: {
      environment: 'jsdom',
      globals: true,
      setupFiles: ['./src/test/setup.ts'],
      include: ['src/**/*.test.{ts,tsx}'],
      // Vitest stubs CSS imports to an empty module by default. Two things here
      // need the real thing: `styles.foo` must be a class name or every
      // className assertion is vacuous, and the theme audit reads palette.css
      // with `?raw` to measure all five palettes at once.
      css: { include: [/.*/], modules: { classNameStrategy: 'non-scoped' } },
    },
  })
);
