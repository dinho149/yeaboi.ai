/**
 * Walking the room, one person's cards at a time.
 *
 * Going round the room card by card is what a retro *is*, and the board hid it
 * in a `<select>` labelled "Show only one person's cards". It was a bar across
 * the top of the board for a while, which is a row of chrome the ceremony pays
 * for the whole time it is running; it is a panel in the notch now, with the
 * other tools, and the board keeps its height.
 *
 * The arrow keys are bound at the document level in App, so stepping does not
 * require the panel to be open, let alone focused.
 */

import { Avatar, Icon } from '../design/primitives';
import { Button } from '../shared';
import styles from './retro.module.css';

export interface FocusControlsProps {
  /** Every author with at least one card, sorted. */
  authors: readonly string[];
  /** The author being walked through, or '' for everyone. */
  current: string;
  avatars: ReadonlyMap<string, string>;
  onStep(delta: number): void;
  onStart(): void;
  onExit(): void;
}

export function FocusControls({ authors, current, avatars, onStep, onStart, onExit }: FocusControlsProps) {
  if (!authors.length) {
    return <p className={styles['popNote']}>Nobody has written a card yet.</p>;
  }

  if (!current) {
    return (
      <div className={styles['focusPanel']}>
        <p className={styles['popNote']}>One person&rsquo;s cards at a time.</p>
        <Button tone="primary" onClick={onStart}>
          Start with {authors[0]}
        </Button>
      </div>
    );
  }

  const position = authors.indexOf(current);

  return (
    <div className={styles['focusPanel']} role="group" aria-label="Walkthrough">
      <div className={styles['focusRow']}>
        <button
          type="button"
          className={styles['focusStep']}
          aria-label="Previous person"
          disabled={authors.length < 2}
          onClick={() => onStep(-1)}
        >
          <Icon name="arrow-left" size={16} />
        </button>

        {/* One live region for the whole state, so stepping announces "Grace, 2
            of 4" as a sentence rather than three separate fragments. */}
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
      </div>

      <Button onClick={onExit}>Show everyone</Button>
    </div>
  );
}
