/**
 * Which retro you are looking at.
 *
 * Two arrows and a label, in the bar beside the sprint. Back walks into the
 * store one retro at a time; forward walks out again, and the last step forward
 * is the live board — which is labelled as such, because "Sprint 42" and
 * "Sprint 41" tell you nothing about which one you can still write on.
 */

import { Icon } from '../design/primitives';
import { cx } from '../runtime/cx';
import type { History } from './useHistory';
import styles from './retro.module.css';

export interface TimeSwitchProps {
  history: History;
  /** What today's board is called, for the live end of the walk. */
  liveLabel: string;
}

export function TimeSwitch({ history, liveLabel }: TimeSwitchProps) {
  const { runs, at, showing, loading } = history;
  // Back is open until the list says otherwise: it is what fetches the list, so
  // disabling it before then would mean nobody could ever ask.
  const canBack = runs.length === 0 ? at === 0 : at < runs.length;
  const run = at > 0 ? runs[at - 1] : undefined;
  const label = at === 0 ? liveLabel : (showing?.sprint_name ?? run?.retro_date ?? '…');

  return (
    <div className={styles['timeSwitch']} role="group" aria-label="Which retro">
      <button
        type="button"
        className={styles['timeStep']}
        aria-label="The retro before this one"
        disabled={!canBack || loading}
        onClick={() => history.step(1)}
      >
        <Icon name="chevron-left" size={14} />
      </button>

      <span className={cx(styles['timeLabel'], at > 0 && styles['timeLabelPast'])} aria-live="polite">
        {label}
      </span>

      <button
        type="button"
        className={styles['timeStep']}
        aria-label={at === 1 ? 'Back to this retro' : 'The retro after this one'}
        disabled={at === 0}
        onClick={() => history.step(-1)}
      >
        <Icon name="chevron-right" size={14} />
      </button>
    </div>
  );
}
