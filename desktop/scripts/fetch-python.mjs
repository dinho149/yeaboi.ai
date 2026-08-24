#!/usr/bin/env node
// Stage the Python the packaged app runs: a python-build-standalone runtime
// with a released `yeaboi` wheel installed into it, at desktop/resources/py/, which
// electron-builder ships as extraResources → Resources/py. sidecar.ts spawns
// `Resources/py/bin/python3 -m yeaboi app` from there.
//
// Two things this deliberately is not. It is not a runtime download: an app
// that pip-installs itself after the installer ran is unsigned code on a signed
// app, and it cannot start on a plane. And it is not a build of the working
// tree: the version installed is a wheel that already exists on PyPI, so what
// ships is a release someone published, not whatever the branch happened to
// contain.
//
// Usage:
//   node scripts/fetch-python.mjs --version 3.28.0 [--platform darwin] [--arch arm64]
//   node scripts/fetch-python.mjs --version 3.28.0 --check   # is the tree staged?

import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { readdir, rm } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { argv, exit, platform as hostPlatform, arch as hostArch } from 'node:process';
import { fileURLToPath } from 'node:url';

const DESKTOP = dirname(dirname(fileURLToPath(import.meta.url)));
const STAGE = join(DESKTOP, 'resources', 'py');
const CACHE = join(DESKTOP, '.python-cache');

// Pinned python-build-standalone release. Bumping it means replacing every
// digest below — they are what makes the download verifiable rather than
// merely successful, and the script prints the one it actually got on a
// mismatch so the swap is mechanical.
const PBS_TAG = '20260814';
const PBS_PYTHON = '3.12.14';
const PBS_BASE = `https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_TAG}`;

/** `${platform}-${arch}` → [rust triple, sha256 of its install_only_stripped tarball]. */
const TARGETS = {
  'darwin-arm64': ['aarch64-apple-darwin', 'dd5b76ab11451a4a4367c17c61d944dded56b425396b07f102922a7ebef7d55f'],
  'darwin-x64': ['x86_64-apple-darwin', 'aec265e3cddaccdb2a3d783331596351b24d4a63c97af0a38f75f643c9451de9'],
  'win32-x64': ['x86_64-pc-windows-msvc', '89f18f6932917163b74339ebcec2645c8e47ae7f1c5f2ac37f2b4f4cf3beb647'],
  'linux-x64': ['x86_64-unknown-linux-gnu', '5acfa3e9ba26b51ae161c83aff278da915b590d22373a424b2ba55b8afe91fcc'],
  'linux-arm64': ['aarch64-unknown-linux-gnu', '2d8e17dfd732102cfeb18e0e1fa6769b24caa034e159981129590fe409c7157a'],
};

// What the desktop needs out of the distribution. `voice` is deliberately
// absent — its model runtime is larger than everything else combined, and the
// app installs it on demand (see the voice setup flow, M11).
const EXTRAS = 'mcp,charts,core';

// Directories inside site-packages that are pure weight. Each `__pycache__`
// also matters on macOS, where every file in the tree gets its own signature.
//
// `pip` is NOT on this list and must not join it: the in-app voice install
// (voice_install.py) falls back to `sys.executable -m pip install`, and inside
// the bundle sys.executable is this interpreter.
const PRUNE = ['__pycache__', 'tests', 'test', 'testing'];

function parseArgs(input = argv.slice(2)) {
  const args = { platform: hostPlatform, arch: hostArch, check: false, version: '' };
  for (let i = 0; i < input.length; i += 1) {
    const flag = input[i];
    if (flag === '--check') args.check = true;
    else if (flag === '--version') args.version = input[(i += 1)] ?? '';
    else if (flag === '--platform') args.platform = input[(i += 1)] ?? '';
    else if (flag === '--arch') args.arch = input[(i += 1)] ?? '';
    else throw new Error(`unknown argument: ${flag}`);
  }
  return args;
}

/** The staged interpreter — the same path sidecar.ts resolves inside the app. */
export function pythonPath(root, platform) {
  return platform === 'win32' ? join(root, 'python.exe') : join(root, 'bin', 'python3');
}

function run(command, args, options = {}) {
  return execFileSync(command, args, { stdio: 'inherit', ...options });
}

async function download(url, into) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url} → HTTP ${response.status}`);
  const body = Buffer.from(await response.arrayBuffer());
  mkdirSync(dirname(into), { recursive: true });
  writeFileSync(into, body);
  return createHash('sha256').update(body).digest('hex');
}

/** Fetch the runtime tarball once, verify it, and keep it for the next run. */
async function fetchRuntime(target) {
  const [triple, digest] = TARGETS[target] ?? [];
  if (!triple) throw new Error(`no python-build-standalone target for ${target}`);
  const name = `cpython-${PBS_PYTHON}+${PBS_TAG}-${triple}-install_only_stripped.tar.gz`;
  const archive = join(CACHE, name);
  if (existsSync(archive)) {
    const cached = createHash('sha256').update(readFileSync(archive)).digest('hex');
    if (cached === digest) return archive;
    rmSync(archive);
  }
  console.log(`fetching ${name}`);
  const got = await download(`${PBS_BASE}/${name}`, archive);
  if (got !== digest) {
    rmSync(archive);
    throw new Error(`checksum mismatch for ${name}\n  expected ${digest}\n  got      ${got}`);
  }
  return archive;
}

/** Everything under `root` whose basename is in PRUNE, deepest first. */
async function prune(root) {
  let removed = 0;
  const walk = async (directory) => {
    let entries;
    try {
      entries = await readdir(directory, { withFileTypes: true });
    } catch {
      return;
    }
    for (const entry of entries) {
      if (!entry.isDirectory()) continue;
      const path = join(directory, entry.name);
      if (PRUNE.includes(entry.name)) {
        await rm(path, { recursive: true, force: true });
        removed += 1;
      } else {
        await walk(path);
      }
    }
  };
  await walk(root);
  return removed;
}

async function stage({ platform, arch, version }) {
  if (!version) throw new Error('--version is required: the released yeaboi wheel to install');
  const target = `${platform}-${arch}`;
  if (platform !== hostPlatform) {
    // uv installs into an interpreter by running it. A Windows python cannot
    // run on macOS, so each platform's bundle is staged on its own runner.
    throw new Error(`cannot stage ${target} on ${hostPlatform}: run this on the target platform`);
  }
  const archive = await fetchRuntime(target);

  rmSync(STAGE, { recursive: true, force: true });
  mkdirSync(STAGE, { recursive: true });
  // The tarball unpacks to a single `python/` directory; --strip-components
  // lands its contents directly in the stage. bsdtar (Windows 10+) and GNU tar
  // both take these flags.
  run('tar', ['-xzf', archive, '-C', STAGE, '--strip-components', '1']);

  const python = pythonPath(STAGE, platform);
  if (!existsSync(python)) throw new Error(`the runtime unpacked without ${python}`);

  console.log(`installing yeaboi[${EXTRAS}]==${version}`);
  run('uv', ['pip', 'install', '--python', python, `yeaboi[${EXTRAS}]==${version}`]);

  const removed = await prune(STAGE);
  console.log(`pruned ${removed} cache/test directories`);
  // Prove the bundle can do the one thing it is here for, before it is signed
  // and long before anyone launches it. `-m` (not the console script, whose
  // shebang is absolute and breaks the moment the app is moved) is exactly how
  // sidecar.ts spawns it, and `app` is a subcommand a wheel older than the
  // desktop simply does not have.
  run(python, ['-c', 'import yeaboi; print("staged yeaboi", yeaboi.__version__)']);
  run(python, ['-m', 'yeaboi', 'app', '--help'], { stdio: 'ignore' });
  writeFileSync(
    join(STAGE, 'yeaboi-bundle.json'),
    `${JSON.stringify({ target, python: PBS_PYTHON, pbs: PBS_TAG, yeaboi: version }, null, 2)}\n`,
  );
}

function check({ platform, arch, version }) {
  const stamp = join(STAGE, 'yeaboi-bundle.json');
  if (!existsSync(stamp)) throw new Error(`nothing staged at ${STAGE} — run without --check first`);
  const staged = JSON.parse(readFileSync(stamp, 'utf8'));
  const target = `${platform}-${arch}`;
  if (staged.target !== target) throw new Error(`staged ${staged.target}, wanted ${target}`);
  if (version && staged.yeaboi !== version) throw new Error(`staged yeaboi ${staged.yeaboi}, wanted ${version}`);
  const python = pythonPath(STAGE, platform);
  if (!statSync(python, { throwIfNoEntry: false })) throw new Error(`missing ${python}`);
  console.log(`✓ ${target}: python ${staged.python}, yeaboi ${staged.yeaboi}`);
}

async function main() {
  const args = parseArgs();
  if (args.check) check(args);
  else await stage(args);
}

// Importable for the tests; only the CLI entry runs anything.
if (process.argv[1] && process.argv[1].endsWith('fetch-python.mjs')) {
  main().catch((error) => {
    console.error(`✗ ${error.message}`);
    exit(1);
  });
}

export { TARGETS, PBS_TAG, PBS_PYTHON, EXTRAS, PRUNE, parseArgs };
