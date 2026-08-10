/**
 * Bringing a plan across from the terminal app.
 *
 * The API took a TUI project id from the start, and there was no way to learn
 * one without a terminal — which made "import" a feature that existed and could
 * not be reached. This is the other half.
 *
 * TODO(design): a list and a button. What this should feel like — a first-run
 * step, a settings action, a drawer — is a product decision, and the structural
 * version does not foreclose any of them.
 */

import { useState } from 'react';

import { Button, Modal } from '../design/primitives';
import { get, post } from './api';
import { AsyncView, EmptyState } from './Slots';
import { useAsync } from './useAsync';
import type { ImportCandidate } from './types';
import styles from './app.module.css';

export function ImportDialog({
  open,
  onClose,
  onImported,
  notify,
}: {
  open: boolean;
  onClose: () => void;
  onImported: () => void;
  notify: (message: string) => void;
}) {
  const [busy, setBusy] = useState('');
  // Keyed on `open` so reopening re-reads: the terminal app may have written a
  // new plan while this dialog was shut.
  const state = useAsync(
    () => get<{ projects: ImportCandidate[] }>('/api/import/candidates'),
    [open],
    { isEmpty: (data) => data.projects.length === 0 },
  );

  async function run(candidate: ImportCandidate) {
    setBusy(candidate.id);
    const result = await post('/api/import/plan', { tui_project_id: candidate.id });
    setBusy('');
    if (!result.ok) {
      notify(result.error);
      return;
    }
    notify(`Imported ${candidate.name}`);
    onImported();
    onClose();
  }

  return (
    <Modal open={open} onClose={onClose} title="Import a plan" footer={<Button onClick={onClose}>Close</Button>}>
      <AsyncView
        state={state}
        empty={
          <EmptyState
            title="Nothing to import"
            hint="Plans made in the terminal app on this machine appear here."
          />
        }
      >
        {(data) => (
          <ul className={styles.projectList}>
            {data.projects.map((candidate) => (
              <li key={candidate.id} className={styles.projectRow}>
                <span className={styles.projectName}>{candidate.name}</span>
                <span className={styles.role}>
                  {candidate.stories} {candidate.stories === 1 ? 'story' : 'stories'}
                </span>
                <Button
                  variant="primary"
                  size="small"
                  busy={busy === candidate.id}
                  onClick={() => run(candidate)}
                >
                  Import
                </Button>
              </li>
            ))}
          </ul>
        )}
      </AsyncView>
    </Modal>
  );
}
