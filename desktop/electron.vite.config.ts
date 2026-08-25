import { resolve } from 'node:path';
import { defineConfig } from 'electron-vite';

// The renderer is a normal Vite ESM app — NOT one of the six committed IIFE
// bundles. Those constraints (file://, tunnel CSP) do not apply inside
// Electron; what does carry over is the design system, which arrives as the
// published @yeaboi-ai/design (mirrored in tsconfig.json "paths").
//
// An alias rather than a bare import, because that package ships SOURCE: Vite
// compiles it here exactly as it compiled the sibling directory this used to
// point at, so moving it between repos changed where the files are and nothing
// about how they are built.
const rendererAliases = {
  react: 'preact/compat',
  'react-dom/client': 'preact/compat/client',
  'react-dom': 'preact/compat',
  'react/jsx-runtime': 'preact/jsx-runtime',
  '@design': resolve(import.meta.dirname, 'node_modules/@yeaboi-ai/design/design'),
};

export default defineConfig({
  main: {
    build: {
      lib: { entry: 'src/main/index.ts' },
    },
  },
  preload: {
    build: {
      // Two preloads: the app's narrow bridge, and the pet's narrower one.
      lib: { entry: { index: 'src/preload/index.ts', pet: 'src/preload/pet.ts' } },
      rollupOptions: {
        // Sandboxed preloads cannot load ESM — emit CommonJS.
        output: { format: 'cjs', entryFileNames: '[name].cjs' },
      },
    },
  },
  renderer: {
    resolve: {
      alias: rendererAliases,
      dedupe: ['preact'],
    },
    server: {
      fs: {
        // The design system is a dependency now, so the dev server no longer
        // needs to serve files from outside this package.
        allow: [import.meta.dirname],
      },
    },
  },
});
