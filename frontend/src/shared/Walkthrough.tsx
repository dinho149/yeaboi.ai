/**
 * Going round the room, one person's cards at a time.
 *
 * A panel in the notch, built from the same parts as the timer's: a labelled
 * field, a row of presets, and one plain button under it. It was a bar across
 * the top of the board for a while — a row of chrome the ceremony pays for the
 * whole time it is running, over the thing everybody is reading.
 *
 * Names rather than arrows. Stepping is what the arrow keys are for, and a
 * facilitator opening this panel is looking for a *person*, not for the next
 * one; picking one directly is a shorter route to the same place and shows the
 * running order at the same time.
 */

import { cx } from '../runtime/cx';
import { Button } from './Button';
import styles from './shared.module.css';

export interface WalkthroughProps {
  /** Everyone with at least one card, in the running order. */
  people: readonly string[];
  /** Whose cards are showing, or '' for everyone's. */
  current: string;
  onPick(name: string): void;
  onExit(): void;
  /** Names the thing being walked through, e.g. "cards". */
  noun?: string;
  className?: string | undefined;
}

export function Walkthrough({ people, current, onPick, onExit, noun = 'cards', className }: WalkthroughProps) {
  if (!people.length) {
    return <p className={styles['panelNote']}>Nobody has written a card yet.</p>;
  }

  return (
    <div className={cx(styles['panelForm'], className)}>
      <div className={styles['field']}>
        <span className={styles['fieldLabel']}>Whose {noun}</span>
        <div className={styles['presetWrap']} role="group" aria-label="Walkthrough">
          {people.map((name) => (
            <button
              key={name}
              type="button"
              className={cx(styles['preset'], name === current && styles['presetOn'])}
              aria-pressed={name === current}
              onClick={() => onPick(name)}
            >
              {name}
            </button>
          ))}
        </div>
      </div>

      <Button disabled={!current} onClick={onExit}>
        Show everyone
      </Button>
    </div>
  );
}
