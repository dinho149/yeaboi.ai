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
 *
 * A lock is the one of those a person did on purpose and can undo, so it is the
 * one that is coloured rather than stated.
 */

import { Icon } from '../design/primitives';
import { cx } from '../runtime/cx';
import { POKER_DECK } from '../types/enums';
import styles from './poker.module.css';

const FAN_CENTRE = (POKER_DECK.length - 1) / 2;

export interface DeckProps {
  /** Your current vote, `''` if you have not voted. */
  mine: string;
  /** Set while a tap is in flight, so the card commits before the round trip. */
  pending: boolean;
  disabled: boolean;
  /** Why the deck is closed. Empty when it is open. */
  reason: string;
  /** The host closed it deliberately — the one reason worth colouring. */
  locked: boolean;
  /** How many people at the table have not voted yet. */
  waiting: number;
  onVote(value: string): void;
}

export function Deck({ mine, pending, disabled, reason, locked, waiting, onVote }: DeckProps) {
  return (
    <div className={styles['deckZone']} data-state={disabled ? 'closed' : 'open'}>
      <p className={cx(styles['deckStatus'], locked && styles['deckStatusLocked'])} role="status">
        {reason ? (
          /* One inline-flex run, so the glyph does not put a stray space in
             front of the sentence a screen reader reads out. */
          <span className={styles['deckReason']}>
            {locked ? <Icon name="lock" size={12} /> : null}
            {reason}
          </span>
        ) : (
          <>
            {mine ? (
              <>
                Your vote: <b className={styles['deckMine']}>{mine}</b> — tap it again to withdraw
              </>
            ) : (
              'Voting open — pick a card'
            )}
            {/* Who the round is waiting for, on the line that says the round is
                open — it is the same fact, and it had a heading of its own
                above the table for no better reason than that it fitted. */}
            <span className={styles['deckWait']}>
              {waiting === 0 ? 'everyone is in' : `${waiting} still to vote`}
            </span>
          </>
        )}
      </p>

      <div className={styles['deck']} role="group" aria-label="Your hand">
        {POKER_DECK.map((value, index) => {
          const selected = value === mine;
          return (
            <button
              key={value}
              type="button"
              className={cx(styles['pcard'], selected && styles['pcardSel'], selected && pending && styles['pcardWait'])}
              // -1 at the left edge, 0 in the middle, 1 at the right: the arc
              // the closed hand is drawn on.
              style={{ '--fan': (index - FAN_CENTRE) / FAN_CENTRE } as never}
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
