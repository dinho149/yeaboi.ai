/**
 * Your hand: the deck of Fibonacci cards, and the line that says why it is open.
 *
 * ## Anatomy
 *
 * Corner index, big centre value, mirrored corner index — a playing card, not a
 * row of buttons. That is the one piece of skeuomorphism in the product and it
 * earns its place: everyone at the table already knows what a hand of cards
 * affords, so nothing about tapping one needs explaining.
 *
 * ## The status line
 *
 * Disabled cards do not explain themselves. A teammate whose deck has gone grey
 * has no way to tell "the host locked voting" from "we already revealed" from
 * "you are previewing a different ticket" — and the last of those is a trap,
 * because a vote always applies to the *live* ticket, never the one you are
 * reading. So the deck says which it is, every time.
 */

import { cx } from '../runtime/cx';
import { POKER_DECK } from '../types/enums';
import { courtName } from './court';
import styles from './poker.module.css';

export interface DeckProps {
  /** Your current vote, `''` if you have not voted. */
  mine: string;
  /** Set while a tap is in flight, so the card commits before the round trip. */
  pending: boolean;
  disabled: boolean;
  /** Why the deck is closed. Empty when it is open. */
  reason: string;
  onVote(value: string): void;
}

export function Deck({ mine, pending, disabled, reason, onVote }: DeckProps) {
  return (
    <div className={styles['deckZone']} data-state={disabled ? 'closed' : 'open'}>
      <p className={styles['deckStatus']} role="status">
        {reason ? (
          reason
        ) : mine ? (
          <>
            Your vote: <b className={styles['deckMine']}>{mine}</b> — tap it again to withdraw
          </>
        ) : (
          'Voting open — pick a card'
        )}
      </p>

      <div className={styles['deck']} role="group" aria-label="Your hand">
        {POKER_DECK.map((value) => {
          const selected = value === mine;
          const court = courtName(value);
          return (
            <button
              key={value}
              type="button"
              className={cx(
                styles['pcard'],
                court && styles['pcardCourt'],
                selected && styles['pcardSel'],
                selected && pending && styles['pcardWait'],
              )}
              disabled={disabled}
              // The label has to say what tapping does, and for the selected
              // card that is the opposite of what it does for every other one.
              aria-label={selected ? `Withdraw your vote of ${value}` : `Vote ${value}`}
              aria-pressed={selected}
              onClick={() => onVote(value)}
            >
              <span className={styles['ci']} aria-hidden="true">
                {value}
              </span>
              <span className={styles['cv']} aria-hidden="true">
                {value}
              </span>
              {court ? (
                <span className={styles['courtName']} aria-hidden="true">
                  {court}
                </span>
              ) : null}
              <span className={cx(styles['ci'], styles['ciFlip'])} aria-hidden="true">
                {value}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
