/**
 * Who is in the room, on hover.
 *
 * The count in the bar is the whole control; pointing at it deals the room out
 * underneath as one thin card per person, each arriving a beat after the one
 * above it. There is no panel behind them — the cards are the list.
 *
 * Hover is not the only way in. The chip is still a button that pins the list
 * open, and focusing it shows the list too, because a keyboard has no pointer
 * and a phone has no hover.
 */

import { Icon } from '../../design/primitives';
import { cx } from '../../runtime/cx';
import type { Participant } from '../Presence';
import styles from './board.module.css';

export interface RoomProps {
  people: readonly Participant[];
  /** Marks one card as "you". */
  meName: string;
}

export function Room({ people, meName }: RoomProps) {
  return (
    <div className={styles['room']}>
      {/* Focusable, and nothing more: a click has no state to leave behind, so
          moving the pointer away closes the room again. Keyboard focus opens it
          through `:has(:focus-visible)`, which a click does not set. */}
      <button type="button" className={styles['presenceChip']} aria-label="Who is in the room">
        <Icon name="users" />
        <span className={styles['roomCount']}>{Math.max(1, people.length)}</span>
      </button>

      <ul className={styles['roomList']}>
        {people.length ? (
          people.map((person, index) => (
            <li key={person.name} className={styles['roomCard']} style={{ '--i': index } as never}>
              <span aria-hidden="true">{person.avatar}</span>
              <span className={styles['roomName']}>{person.name}</span>
              {person.name === meName ? <span className={styles['roomYou']}>you</span> : null}
            </li>
          ))
        ) : (
          <li className={cx(styles['roomCard'], styles['roomEmpty'])} style={{ '--i': 0 } as never}>
            Nobody here yet
          </li>
        )}
      </ul>
    </div>
  );
}
