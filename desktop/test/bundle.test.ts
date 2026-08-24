// The Python staging step's decisions. The download and the install are a
// network and a subprocess; what is worth pinning is the pin itself — every
// platform the release workflow builds must have a runtime and a checksum
// here, and the interpreter path must be the one sidecar.ts spawns.

import { describe, expect, it } from 'vitest';
// @ts-expect-error — a plain .mjs script, deliberately untyped
import { EXTRAS, PBS_PYTHON, PBS_TAG, PRUNE, TARGETS, parseArgs, pythonPath } from '../scripts/fetch-python.mjs';

describe('TARGETS', () => {
  it('covers every platform the release matrix builds', () => {
    expect(Object.keys(TARGETS).sort()).toEqual([
      'darwin-arm64',
      'darwin-x64',
      'linux-arm64',
      'linux-x64',
      'win32-x64',
    ]);
  });

  it('pins a sha256 for each — a download that is merely successful is not verified', () => {
    for (const [target, [triple, digest]] of Object.entries(TARGETS) as [string, [string, string]][]) {
      expect(triple, target).toMatch(/^[a-z0-9_]+-[a-z0-9-]+$/);
      expect(digest, target).toMatch(/^[0-9a-f]{64}$/);
    }
  });

  it('pins one runtime release, not a floating latest', () => {
    expect(PBS_TAG).toMatch(/^\d{8}$/);
    expect(PBS_PYTHON).toMatch(/^3\.\d+\.\d+$/);
  });
});

describe('EXTRAS', () => {
  it('ships what the desktop uses and leaves voice to be installed on demand', () => {
    expect(EXTRAS.split(',').sort()).toEqual(['charts', 'mcp']);
    expect(EXTRAS).not.toContain('voice');
  });
});

describe('PRUNE', () => {
  it('drops the trees that are pure weight to sign', () => {
    expect(PRUNE).toContain('__pycache__');
    expect(PRUNE).toContain('tests');
  });

  it('keeps pip — the in-app voice install runs `sys.executable -m pip`', () => {
    expect(PRUNE).not.toContain('pip');
  });
});

describe('pythonPath', () => {
  it('is the path sidecar.ts spawns inside the packaged app', () => {
    // sidecar.ts: `${process.resourcesPath}/py/bin/python3`, or python.exe.
    expect(pythonPath('/r/py', 'darwin')).toBe('/r/py/bin/python3');
    expect(pythonPath('/r/py', 'linux')).toBe('/r/py/bin/python3');
    expect(pythonPath('/r/py', 'win32').replace(/\\/g, '/')).toBe('/r/py/python.exe');
  });
});

describe('parseArgs', () => {
  it('reads the release to bundle and the target to bundle it for', () => {
    const args = parseArgs(['--version', '3.28.0', '--platform', 'win32', '--arch', 'x64']);
    expect(args).toMatchObject({ version: '3.28.0', platform: 'win32', arch: 'x64', check: false });
  });

  it('refuses an argument it does not know instead of ignoring it', () => {
    expect(() => parseArgs(['--platfrom', 'win32'])).toThrow(/unknown argument/);
  });
});
