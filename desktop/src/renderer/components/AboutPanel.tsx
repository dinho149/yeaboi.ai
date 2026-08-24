// About: what is running, and the only place the app updates itself from.
//
// The terminal's Ctrl+U pip-installs and relaunches. A signed bundle cannot be
// rewritten in place, so the desktop replaces the whole application instead —
// same promise, different mechanism, and it says which version it is going to.

import { Duck } from '@design/primitives/Duck';
import { useEffect, useState } from 'react';
import {
  type ShellMeta,
  type UpdateState,
  type VersionMeta,
  checkForUpdate,
  downloadUpdate,
  getShellMeta,
  getUpdateState,
  getVersion,
  installUpdate,
  onUpdateState,
} from '../api';
import { openShortcuts } from '../palette';
import { aboutRows, updateAction, updateHeadline, updatePercent } from '../updates';

export function AboutPanel({ onClose }: { onClose: () => void }) {
  const [shell, setShell] = useState<ShellMeta | null>(null);
  const [backend, setBackend] = useState<VersionMeta | null>(null);
  const [update, setUpdate] = useState<UpdateState>({ kind: 'idle' });

  useEffect(() => {
    getShellMeta().then(setShell, () => undefined);
    getVersion().then(setBackend, () => undefined);
    getUpdateState().then(setUpdate, () => undefined);
    onUpdateState(setUpdate);
  }, []);

  const { action, label } = updateAction(update);
  const percent = updatePercent(update);
  const run = (): void => {
    if (action === 'check') void checkForUpdate().then(setUpdate);
    else if (action === 'download') void downloadUpdate().then(setUpdate);
    else if (action === 'install') void installUpdate();
  };

  return (
    <div class="scrim" onClick={onClose}>
      <div
        class="modal about"
        role="dialog"
        aria-modal="true"
        aria-label="About yeaboi"
        onClick={(event) => event.stopPropagation()}
      >
        <header class="modal-head">
          <h2>About yeaboi</h2>
          <button type="button" class="link" onClick={onClose}>
            Close
          </button>
        </header>
        <div class="about-brand">
          <Duck state="idle" size={56} />
          <p class="about-tagline">AI Scrum Master, and a watcher of AI coding agents.</p>
        </div>
        <dl class="about-rows">
          {aboutRows(shell, backend).map(([name, value]) => (
            <div key={name}>
              <dt>{name}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
        <div class="about-update">
          <p class={update.kind === 'error' ? 'about-update-line error' : 'about-update-line'}>
            {updateHeadline(update, shell?.version ?? '')}
          </p>
          {percent !== null && (
            <div class="about-progress" role="progressbar" aria-valuenow={percent}>
              <span style={{ width: `${percent}%` }} />
            </div>
          )}
          {action !== 'none' && (
            <button type="button" class="primary" onClick={run}>
              {label}
            </button>
          )}
        </div>
        <footer class="modal-actions">
          <button type="button" class="link" onClick={openShortcuts}>
            Keyboard shortcuts
          </button>
        </footer>
      </div>
    </div>
  );
}
