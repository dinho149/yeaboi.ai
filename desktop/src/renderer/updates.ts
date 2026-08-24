// What the About panel says about an update, and which single button it offers.
// Kept out of the component so the wording is testable: an update UI is mostly
// sentences, and the states it has to be honest about (no update, one waiting,
// one downloading, a channel that cannot update at all) never all occur while
// anybody is looking.

import type { ShellMeta, UpdateState, VersionMeta } from './api';

export type UpdateAction = 'check' | 'download' | 'install' | 'none';

/** One line, in the person's terms — never the state's name. */
export function updateHeadline(state: UpdateState, current: string): string {
  switch (state.kind) {
    case 'unsupported':
      return state.reason;
    case 'checking':
      return 'Looking for a newer version…';
    case 'available':
      return `yeaboi ${state.version} is available.`;
    case 'downloading':
      return `Downloading ${state.version} — ${state.percent}%`;
    case 'ready':
      return `yeaboi ${state.version} is ready. Restart to finish.`;
    case 'error':
      return `Could not check for updates: ${state.message}`;
    default:
      return current ? `yeaboi ${current} is the latest version.` : 'Up to date.';
  }
}

/** The one thing the button does next, or nothing at all. */
export function updateAction(state: UpdateState): { action: UpdateAction; label: string } {
  switch (state.kind) {
    case 'available':
      return { action: 'download', label: 'Download' };
    case 'ready':
      return { action: 'install', label: 'Restart now' };
    case 'checking':
    case 'downloading':
    case 'unsupported':
      return { action: 'none', label: '' };
    default:
      return { action: 'check', label: 'Check for updates' };
  }
}

/** Progress as a fraction for the bar, or null when there is nothing to show. */
export function updatePercent(state: UpdateState): number | null {
  return state.kind === 'downloading' ? Math.max(0, Math.min(100, state.percent)) : null;
}

/** The rows of the About panel: what is running, and what it is running on. */
export function aboutRows(shell: ShellMeta | null, backend: VersionMeta | null): [string, string][] {
  const rows: [string, string][] = [];
  if (shell) {
    rows.push(['App', `${shell.version}${shell.packaged ? '' : ' (dev)'}`]);
    rows.push(['Platform', `${shell.platform} ${shell.arch}`]);
    rows.push(['Electron', `${shell.electron} · Chromium ${shell.chrome}`]);
  }
  if (backend) {
    rows.push(['yeaboi', backend.version]);
    rows.push(['Python', backend.python]);
    rows.push(['Session schema', String(backend.schema_version)]);
  }
  return rows;
}
