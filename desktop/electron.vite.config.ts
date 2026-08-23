import { resolve } from 'node:path';
import { defineConfig } from 'electron-vite';

// The renderer is a normal Vite ESM app — NOT one of the six committed IIFE
// bundles. Those constraints (file://, tunnel CSP) do not apply inside
// Electron; what does carry over is the design system, imported straight from
// frontend/src via the aliases below (mirrored in tsconfig.json "paths").
const rendererAliases = {
  react: 'preact/compat',
  'react-dom/client': 'preact/compat/client',
  'react-dom': 'preact/compat',
  'react/jsx-runtime': 'preact/jsx-runtime',
  '@design': resolve(import.meta.dirname, '../frontend/src/design'),
  '@shared': resolve(import.meta.dirname, '../frontend/src/shared'),
};

export default defineConfig({
  main: {
    build: {
      lib: { entry: 'src/main/index.ts' },
    },
  },
  preload: {
    build: {
      lib: { entry: 'src/preload/index.ts' },
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
        // The design system lives one directory up, in frontend/src.
        allow: [resolve(import.meta.dirname, '..')],
      },
    },
  },
});
