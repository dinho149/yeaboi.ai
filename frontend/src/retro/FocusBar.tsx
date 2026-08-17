/**
 * The walkthrough bar: one person's cards at a time.
 *
 * Going round the room card by card is what a retro *is*, and the board hid it
 * in a `<select>` labelled "Show only one person's cards", wedged between the
 * presence row and the toolbar. Almost nobody found it.
 *
 * Promoting it costs one row of chrome that only exists while a walkthrough is
 * running, and buys the actual ceremony a first-class control: who is up, how
 * far through the room you are, ← / → to step, Escape to drop back to everyone.
 * The arrow keys are bound at the document level in App, so stepping does not
 * require keeping focus on this bar while you read the cards.
 */

import { Avatar, Icon } from '../design/primitives';
import { Button } from '../shared';
import styles from './retro.module.css';

export interface FocusBarProps {
  /** Every author with at least one card, sorted. */
  authors: readonly string[];
  /** The author being walked through. */
  current: string;
  avatars: ReadonlyMap<string, string>;
  onStep(delta: number): void;
  onExit(): void;
}

export function FocusBar({ authors, current, avatars, onStep, onExit }: FocusBarProps) {
  const position = authors.indexOf(current);

  return (
    <div className={styles['focusBar']} role="region" aria-label="Walkthrough">
      <button
        type="button"
        className={styles['focusStep']}
        aria-label="Previous person"
        disabled={authors.length < 2}
        onClick={() => onStep(-1)}
      >
        <Icon name="arrow-left" size={16} />
      </button>

      {/* One live region for the whole state, so stepping announces "Grace, 2 of
          4" as a sentence rather than three separate fragments. */}
      <p className={styles['focusWho']} aria-live="polite" aria-atomic="true">
        <Avatar name={current} emoji={avatars.get(current)} size={22} />
        <strong className={styles['focusName']}>{current}</strong>
        <span className={styles['focusCount']}>
          {position + 1} of {authors.length}
        </span>
      </p>

      <button
        type="button"
        className={styles['focusStep']}
        aria-label="Next person"
        disabled={authors.length < 2}
        onClick={() => onStep(1)}
      >
        <Icon name="arrow-right" size={16} />
      </button>

      <Button onClick={onExit}>
        Show everyone
      </Button>
    </div>
  );
}
