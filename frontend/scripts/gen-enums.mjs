/**
 * Render `src/types/enums.ts` from the enums contract.
 *
 * The contract (`contracts/web/enums.json`) is written by Python and carries
 * data only — names, values, and the sentence each is documented with. Every
 * TypeScript decision lives here: the `as const`, the element-type aliases and
 * how they are spelled, the JSDoc layout. That split is what lets this package
 * be a repo with no Python in it: it vendors the JSON and renders from its own
 * copy.
 *
 * Usage:
 *   node scripts/gen-enums.mjs           # write the file
 *   node scripts/gen-enums.mjs --check   # exit non-zero if it is stale (CI)
 */

import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const CONTRACT = resolve(HERE, '../../contracts/web/enums.json');
const OUTPUT = resolve(HERE, '../src/types/enums.ts');

/** The block kinds this renderer knows how to draw. */
const SCHEMA = 1;

const HEADER = `/*
 * GENERATED FILE — do not edit.
 *
 * Rendered from contracts/web/enums.json, which Python writes from the tuples
 * the server validates against. A literal union that disagreed with one would
 * let the client offer a value the board will always refuse.
 *
 * Regenerate both halves with \`make web-types\`; CI runs both with --check.
 *
 * Only the enums are generated. State shapes are hand-written in ./board.ts,
 * because they carry semantics a codegen cannot express — and a confidently
 * wrong generated interface is worse than an honest hand-written one.
 */
`;

/** `RETRO_GRIDS` → `RetroGrids`. The one naming rule, applied in one place. */
const typeName = (name) =>
  name
    .toLowerCase()
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join('');

/** A JSON string, spelled the way Python's `json.dumps` spells it. */
const str = (value) => JSON.stringify(value);

/** An array on one line, with Python's `", "` separator rather than JS's `","`. */
const arr = (values) => `[${values.map(str).join(', ')}]`;

/** One value of a lookup table: a string, an array of strings, or null. */
const entry = (value) => (Array.isArray(value) ? arr(value) : str(value));

/** One line stays inline; several become a block. Blank lines carry no space. */
const jsdoc = (lines) =>
  lines.length === 1
    ? `/** ${lines[0]} */\n`
    : `/**\n${lines.map((line) => (line ? ` * ${line}` : ' *')).join('\n')}\n */\n`;

/**
 * Lookup-table rows.
 *
 * The contract sends `[key, value]` pairs rather than an object because JS
 * hoists integer-like keys: read as an object, `BLOCK_GLYPHS` would come back
 * with its ten digits ahead of its letters. Keep them pairs all the way here.
 */
const rows = (pairs) =>
  pairs.map(([key, value]) => `  ${str(key)}: ${entry(value)},\n`).join('');

const RENDERERS = {
  tuple: (b) =>
    `${jsdoc(b.doc)}export const ${b.name} = ${arr(b.values)} as const;\n` +
    `export type ${typeName(b.name)} = (typeof ${b.name})[number];\n`,

  labels: (b) =>
    `${jsdoc(b.doc)}export const ${b.name}: Record<${typeName(b.keys)}, string> = {\n` +
    `${rows(b.labels)}};\n`,

  table: (b) =>
    `${jsdoc(b.doc)}export const ${b.name}: Record<string, ${b.value}> = {\n` +
    `${rows(b.entries)}};\n`,
};

function render() {
  const contract = JSON.parse(readFileSync(CONTRACT, 'utf-8'));
  if (contract.$schema_version !== SCHEMA) {
    throw new Error(
      `enums.json is schema ${contract.$schema_version}, this renderer speaks ${SCHEMA} — ` +
        'the contract grew a block kind. Teach RENDERERS about it and bump SCHEMA.',
    );
  }
  const blocks = contract.blocks.map((block) => {
    const draw = RENDERERS[block.kind];
    if (!draw) throw new Error(`unknown block kind "${block.kind}" in ${block.name}`);
    return draw(block);
  });
  return [HEADER, ...blocks].join('\n');
}

const generated = render();
const current = (() => {
  try {
    return readFileSync(OUTPUT, 'utf-8');
  } catch {
    return '';
  }
})();

if (process.argv.includes('--check')) {
  if (current === generated) {
    console.log('✓ enums.ts is up to date');
  } else {
    console.error('✗ enums.ts is stale — run: make web-types');
    process.exit(1);
  }
} else if (current === generated) {
  console.log('✓ enums.ts unchanged');
} else {
  writeFileSync(OUTPUT, generated, 'utf-8');
  console.log('✓ wrote enums.ts');
}
