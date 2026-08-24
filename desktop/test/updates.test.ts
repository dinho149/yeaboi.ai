// What the About panel and the tray say about an update, and where an update
// is possible at all. The download itself is electron-updater's business; the
// sentences around it are ours, and they are the part that has to be right on
// the one day someone reads them.

import { describe, expect, it, vi } from 'vitest';
import type { ShellMeta, UpdateState, VersionMeta } from '../src/renderer/api';
import { aboutRows, updateAction, updateHeadline, updatePercent } from '../src/renderer/updates';

vi.mock('electron', () => ({ app: { isPackaged: false, getVersion: () => '0.0.0' } }));
const { updateSupport } = await import('../src/main/updater');
const { updateLabel } = await import('../src/main/tray');

const shell: ShellMeta = {
  version: '3.28.0',
  electron: '38.2.0',
  chrome: '140.0.0',
  platform: 'darwin',
  arch: 'arm64',
  packaged: true,
};
const backend: VersionMeta = { version: '3.28.0', schema_version: 30, python: '3.12.14' };

describe('updateHeadline', () => {
  it('names the version rather than the state', () => {
    expect(updateHeadline({ kind: 'available', version: '3.29.0' }, '3.28.0')).toBe('yeaboi 3.29.0 is available.');
    expect(updateHeadline({ kind: 'ready', version: '3.29.0' }, '3.28.0')).toContain('Restart to finish');
  });

  it('says what is running when there is nothing to do', () => {
    expect(updateHeadline({ kind: 'idle' }, '3.28.0')).toBe('yeaboi 3.28.0 is the latest version.');
  });

  it('passes an error through instead of swallowing it', () => {
    expect(updateHeadline({ kind: 'error', message: 'ENOTFOUND' }, '3.28.0')).toContain('ENOTFOUND');
  });

  it('explains an unsupported channel in the words the channel gave', () => {
    const state: UpdateState = { kind: 'unsupported', reason: 'Installed from a package.' };
    expect(updateHeadline(state, '3.28.0')).toBe('Installed from a package.');
  });
});

describe('updateAction', () => {
  it('offers exactly one next step per state', () => {
    expect(updateAction({ kind: 'idle' }).action).toBe('check');
    expect(updateAction({ kind: 'available', version: '1.0.0' }).action).toBe('download');
    expect(updateAction({ kind: 'ready', version: '1.0.0' }).action).toBe('install');
    expect(updateAction({ kind: 'error', message: 'x' }).action).toBe('check');
  });

  it('offers nothing while something is already happening', () => {
    expect(updateAction({ kind: 'checking' }).action).toBe('none');
    expect(updateAction({ kind: 'downloading', version: '1.0.0', percent: 4 }).action).toBe('none');
    expect(updateAction({ kind: 'unsupported', reason: 'x' }).action).toBe('none');
  });
});

describe('updatePercent', () => {
  it('is null unless a download is running', () => {
    expect(updatePercent({ kind: 'available', version: '1.0.0' })).toBeNull();
    expect(updatePercent({ kind: 'downloading', version: '1.0.0', percent: 42 })).toBe(42);
  });

  it('clamps a percentage the updater exaggerated', () => {
    expect(updatePercent({ kind: 'downloading', version: '1.0.0', percent: 140 })).toBe(100);
    expect(updatePercent({ kind: 'downloading', version: '1.0.0', percent: -3 })).toBe(0);
  });
});

describe('aboutRows', () => {
  it('carries both halves — the shell and the yeaboi it runs', () => {
    const rows = new Map(aboutRows(shell, backend));
    expect(rows.get('App')).toBe('3.28.0');
    expect(rows.get('yeaboi')).toBe('3.28.0');
    expect(rows.get('Python')).toBe('3.12.14');
    expect(rows.get('Session schema')).toBe('30');
  });

  it('marks a dev run so a version mismatch is not a mystery', () => {
    const rows = new Map(aboutRows({ ...shell, packaged: false }, backend));
    expect(rows.get('App')).toBe('3.28.0 (dev)');
  });

  it('shows what it has when the backend has not answered yet', () => {
    expect(aboutRows(shell, null).map(([name]) => name)).not.toContain('Python');
    expect(aboutRows(null, null)).toEqual([]);
  });
});

describe('updateSupport', () => {
  it('lets a packaged mac or windows build update itself', () => {
    expect(updateSupport(true, 'darwin', undefined)).toBeNull();
    expect(updateSupport(true, 'win32', undefined)).toBeNull();
  });

  it('lets an AppImage update itself and leaves a deb to the package manager', () => {
    expect(updateSupport(true, 'linux', '/tmp/yeaboi.AppImage')).toBeNull();
    expect(updateSupport(true, 'linux', undefined)).toContain('package manager');
  });

  it('says so rather than offering a button that cannot work in dev', () => {
    expect(updateSupport(false, 'darwin', undefined)).toContain('dev server');
  });
});

describe('updateLabel', () => {
  it('tells the tray what the click will do next', () => {
    expect(updateLabel({ kind: 'idle' })).toBe('Check for updates…');
    expect(updateLabel({ kind: 'available', version: '3.29.0' })).toBe('Download yeaboi 3.29.0');
    expect(updateLabel({ kind: 'downloading', version: '3.29.0', percent: 12 })).toContain('12%');
    expect(updateLabel({ kind: 'ready', version: '3.29.0' })).toContain('Restart');
  });

  it('admits a failed check instead of looking idle', () => {
    expect(updateLabel({ kind: 'error', message: 'nope' })).toContain('failed');
  });
});
