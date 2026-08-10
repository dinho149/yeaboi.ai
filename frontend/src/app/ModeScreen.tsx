/**
 * One mode's screen.
 *
 * Deliberately thin right now, and honest about it. The mode map
 * (`~/yeaboi-mode-map/`) established that each mode has a headless engine the
 * app can call — `reporting.engine:run_delivery_report` even has progress and
 * cancellation — but running one from a browser needs a job runner that does
 * not exist yet: these take minutes, make LLM calls, and cannot be held open
 * on a request.
 *
 * So this screen currently says what the mode is and what it can do here. That
 * is worth shipping ahead of the runner because the alternative — a card that
 * navigates nowhere — is what made the app feel empty.
 *
 * TODO(design): per-mode screens are the substance of the port. Each grows a
 * form (the TUI's prompts), a run button, a progress view, and its result.
 */

import { Button } from '../design/primitives';
import { get } from './api';
import { navigate } from './router';
import { AsyncView, EmptyState } from './Slots';
import { useAsync } from './useAsync';
import type { ModeCard } from './types';
import styles from './app.module.css';

export function ModeScreen({ modeKey }: { modeKey: string }) {
  const state = useAsync(() => get<{ modes: ModeCard[] }>('/api/modes'), [modeKey]);

  return (
    <AsyncView state={state} empty={<EmptyState title="No modes" />}>
      {(data) => {
        const mode = data.modes.find((candidate) => candidate.key === modeKey);
        if (!mode) {
          return <EmptyState title="No such mode" hint={modeKey} />;
        }
        return (
          <section {...(mode.accent ? { 'data-mode': mode.accent } : {})}>
            <h1>{mode.title}</h1>
            <p className={styles.modeDescription}>{mode.description}</p>
            {mode.note ? <p className={styles.modeNote}>{mode.note}</p> : null}
            <div className={styles.toolbar}>
              {mode.support === 'view' ? (
                <Button tone="primary" size="s" onClick={() => navigate('/projects')}>
                  See what is here
                </Button>
              ) : null}
              <Button size="s" onClick={() => navigate('/')}>
                Back
              </Button>
            </div>
          </section>
        );
      }}
    </AsyncView>
  );
}
