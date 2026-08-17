/**
 * Which retro you are looking at.
 *
 * Two arrows and a picker, in the bar beside the sprint. The arrows are for
 * "the one before this", which is the step people actually take; the picker is
 * for "the one where we talked about the deploy", which is the one they
 * remember by name.
 *
 * The live board is an option in the list, not a separate control — going back
 * to today is the same kind of move as going anywhere else, and a picker that
 * cannot express where you already are is a picker with a hole in it.
 */

import { Dropdown, Icon } from '../design/primitives';
import { cx } from '../runtime/cx';
import type { History } from './useHistory';
import styles from './retro.module.css';

export interface TimeSwitchProps {
  history: History;
  /** What today's board is called, for the live end of the walk. */
  liveLabel: string;
}

/** Labels have to be unique — two retros in one sprint would collide. */
function labelFor(run: { retro_date: string; sprint_name?: string }, index: number): string {
  const named = run.sprint_name?.trim();
  const when = run.retro_date || '—';
  return named ? `${named} · ${when}` : `${when}${index ? ` (${index})` : ''}`;
}

export function TimeSwitch({ history, liveLabel }: TimeSwitchProps) {
  const { runs, at, loading } = history;
  // Back is open until the list says otherwise: it is what fetches the list, so
  // disabling it before then would mean nobody could ever ask.
  const canBack = runs.length === 0 ? at === 0 : at < runs.length;

  const options = [liveLabel, ...runs.map(labelFor)];
  const value = options[at] ?? liveLabel;

  return (
    <span className={styles['timeSwitch']} role="group" aria-label="Which retro">
      <button
        type="button"
        className={styles['timeStep']}
        aria-label="The retro before this one"
        disabled={!canBack || loading}
        onClick={() => history.step(1)}
      >
        <Icon name="chevron-left" size={14} />
      </button>

      <Dropdown
        label="Which retro"
        value={value}
        options={options}
        className={cx(styles['timePick'], at > 0 && styles['timePickPast'])}
        onChange={(next) => history.go(options.indexOf(next))}
      />

      <button
        type="button"
        className={styles['timeStep']}
        aria-label={at === 1 ? 'Back to this retro' : 'The retro after this one'}
        disabled={at === 0}
        onClick={() => history.step(-1)}
      >
        <Icon name="chevron-right" size={14} />
      </button>
    </span>
  );
}
