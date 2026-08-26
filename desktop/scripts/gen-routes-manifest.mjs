// Code-generate contracts/v1/routes_manifest.json from the renderer's
// routes.json — the same build-and-commit seam as frontend/ → web/static/.
// `--check` fails when the committed manifest is stale (CI runs it via
// `make desktop-check`; the Python suite reads the committed file and never
// needs Node).

import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { exit } from 'node:process';

const here = import.meta.dirname;
const source = resolve(here, '../src/renderer/routes.json');
const target = resolve(here, '../../contracts/v1/routes_manifest.json');

const registry = JSON.parse(readFileSync(source, 'utf-8'));
const rendered = `${JSON.stringify(registry, null, 2)}\n`;

if (process.argv.includes('--check')) {
  const committed = readFileSync(target, 'utf-8');
  if (committed !== rendered) {
    console.error(`stale manifest: ${target}\nrun \`npm run gen-manifest\` (in desktop/) and commit the result`);
    exit(1);
  }
  console.log('routes manifest is current');
} else {
  writeFileSync(target, rendered);
  console.log(`wrote ${target}`);
}
