import { defineConfig } from 'vite';

// @ts-expect-error — plain .mjs, shared with build-all.mjs which cannot import TS.
import { ENTRIES, globalName } from './entries.mjs';

/**
 * One config, one entry per invocation (`vite build --mode <name>`).
 *
 * Almost every option below is load-bearing rather than preference — the pages
 * these bundles land in are opened over `file://` and served through a tunnel
 * with a strict CSP, so the usual defaults are all wrong. Read the comments
 * before changing anything here.
 */
/**
 * Strip vendor documentation URLs out of the built bundle.
 *
 * `test_bundle_fetches_nothing` greps the minified output for any external
 * origin, because once esbuild has been through it there is no way to tell an
 * `<a href>` from a `fetch()`. GSAP embeds `https://gsap.com` in a console
 * warning ("GSAP target not found"), which is a *sentence*, not a request — but
 * the guard cannot know that, and the project already spends its one carve-out
 * on the footer credit.
 *
 * Widening the guard to allow a second origin would cost it its teeth. Removing
 * the string costs a broken link in a console message nobody reads in
 * production, and leaves the bundle honestly free of external origins rather
 * than exempted from the check. So: delete it, and keep the guard blunt.
 */
const stripVendorDocUrls = () => ({
  name: 'yeaboi:strip-vendor-doc-urls',
  renderChunk(code: string) {
    // split/join rather than replaceAll: the tsconfig lib target predates it.
    return { code: code.split('https://gsap.com').join(''), map: null };
  },
});

export default defineConfig(({ mode, command }) => {
  const entry = ENTRIES[mode as keyof typeof ENTRIES];
  if (command === 'build' && !entry) {
    throw new Error(
      `unknown build mode "${mode}" — known modes: ${Object.keys(ENTRIES).join(', ')}`
    );
  }

  return {
    plugins: [stripVendorDocUrls()],

    // Vite mistakes the repo root for the project root otherwise and starts
    // hunting for a index.html at the wrong level.
    root: import.meta.dirname,

    // Preact, not React. Identical API, identical TSX, identical @types/react,
    // but ~16 KB instead of ~190 KB — and every one of these bundles is inlined
    // into a page that is either emailed around as a file or pulled over a
    // phone tunnel, so the runtime is paid for on every single copy. One-line
    // flip back to React if a React-internals-dependent library ever appears.
    // Longest keys first: alias matching is prefix-based, so a bare `react-dom`
    // entry would otherwise swallow `react-dom/client`.
    // The matching half lives in tsconfig.json `paths` — change both or `tsc`
    // and the bundler start resolving different modules.
    resolve: {
      alias: {
        'react-dom/client': 'preact/compat/client',
        'react/jsx-runtime': 'preact/jsx-runtime',
        react: 'preact/compat',
        'react-dom': 'preact/compat',
      },
    },

    // Vite does NOT substitute process.env in lib mode, and React/Preact-compat
    // reads it at module scope — without this the page dies at boot with
    // "ReferenceError: process is not defined".
    define: { 'process.env.NODE_ENV': '"production"' },

    server: {
      // NOT Vite's default 5173: retro/server.py already claims that port, so
      // the dev server and the board it proxies to would fight over it.
      port: 5399,
      strictPort: true,
      // Retro's board is the default because it is the one most people are
      // working on, but poker serves the same /api on :5273 — so the target is
      // a variable rather than a constant:
      //
      //   YEABOI_DEV_API=http://127.0.0.1:5273 make web-dev
      //
      // Hardcoding retro's port is part of why poker drifted: pointing the dev
      // server at the poker board meant editing this file.
      proxy: { '/api': process.env['YEABOI_DEV_API'] ?? 'http://127.0.0.1:5173' },
    },

    build: {
      outDir: '../src/yeaboi/web/static',
      // Each mode is a separate invocation writing into the same directory —
      // emptying it would delete the previous entry's output.
      emptyOutDir: false,
      target: 'es2020',
      sourcemap: false,
      // Nothing may be fetched at runtime: no CDN, no <link>, no dynamic import.
      modulePreload: false,
      cssCodeSplit: false,
      // Any imported asset becomes a data: URI rather than a second file —
      // the pages must stay single-document self-contained.
      assetsInlineLimit: 100_000_000,
      // JS is minified; CSS deliberately is not. The committed bundles land in
      // git, and an unminified stylesheet keeps the diff reviewable and the
      // merge conflicts resolvable. It also keeps `[data-theme="light"]`
      // literally intact, which the Python contract tests assert on.
      minify: 'esbuild',
      cssMinify: false,
      lib: {
        entry: entry ?? Object.values(ENTRIES)[0],
        name: globalName(mode),
        formats: ['iife'],
        fileName: () => `${mode}.js`,
      },
      rollupOptions: {
        output: {
          inlineDynamicImports: true,
          // Force `<mode>.css` — Vite's lib mode would otherwise name it
          // style.css and every entry would clobber the last one.
          assetFileNames: `${mode}.[ext]`,
        },
      },
    },
  };
});
