// Build every entry in entries.mjs, one `vite build --mode <name>` per entry.
//
// Why not a single build: rollup rejects IIFE output when there is more than
// one input, and IIFE is required (file:// + CSP). See entries.mjs.
import { spawnSync } from 'node:child_process';
import { ENTRIES } from './entries.mjs';

const vite = process.platform === 'win32' ? 'vite.cmd' : 'vite';

for (const mode of Object.keys(ENTRIES)) {
  console.log(`\n→ building "${mode}"`);
  const res = spawnSync(vite, ['build', '--mode', mode], {
    stdio: 'inherit',
    cwd: import.meta.dirname,
    // node_modules/.bin is on PATH via npm run.
    shell: process.platform === 'win32',
  });
  if (res.status !== 0) {
    console.error(`build failed for "${mode}"`);
    process.exit(res.status ?? 1);
  }
}
console.log(`\n✓ built ${Object.keys(ENTRIES).length} entr${Object.keys(ENTRIES).length === 1 ? 'y' : 'ies'}`);
