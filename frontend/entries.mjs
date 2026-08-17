// The single source of truth for what gets built.
//
// Rollup refuses IIFE output with multiple inputs, and IIFE is non-negotiable
// (exports open over file://, where a type="module" script simply does not
// execute, and the tunnel CSP forbids eval). So each entry is its own
// `vite build --mode <name>` invocation — see build-all.mjs — and this map is
// imported by BOTH that script and vite.config.ts so the two can never
// disagree about which modes exist.
//
export const ENTRIES = {
  deck: 'src/deck/main.tsx',
  export: 'src/export/main.tsx',
  gate: 'src/gate/main.tsx',
  poker: 'src/poker/main.tsx',
  retro: 'src/retro/main.tsx',
  ship: 'src/ship/main.tsx',
};

/** Global name for the IIFE wrapper. Nothing reads it; rollup requires one. */
export const globalName = (mode) => `yeaboi_${mode}`;
