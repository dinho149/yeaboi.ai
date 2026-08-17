/**
 * Going round the room, one person's cards at a time.
 *
 * A panel in the notch. It was a bar across the top of the board — a row of
 * chrome the ceremony pays for the whole time it is running, over the thing
 * everybody is reading.
 *
 * Faces and counts, not a list of names. A facilitator picking who goes next is
 * reading two things off this panel: who has not been round yet, and how much
 * they have to get through. A row of name buttons answers neither, and the
 * arrow keys already handle "the next one".
 */

import { Avatar } from '../design/primitives';
import { cx } from '../runtime/cx';
import { Button } from '../shared';
import styles from './retro.module.css';

export interface Person {
  name: string;
  avatar?: string | undefined;
  /** How many cards they wrote. */
  cards: number;
}

export interface WalkthroughProps {
  people: readonly Person[];
  /** Whose cards are showing, or '' for everyone's. */
  current: string;
  onPick(name: string): void;
  onExit(): void;
}

export function Walkthrough({ people, current, onPick, onExit }: WalkthroughProps) {
  if (!people.length) {
    return <p className={styles['popNote']}>Nobody has written a card yet.</p>;
  }

  return (
    <div className={styles['walk']}>
      <div className={styles['walkGrid']} role="group" aria-label="Walkthrough">
        {people.map((person) => (
          <button
            key={person.name}
            type="button"
            className={cx(styles['walkPerson'], person.name === current && styles['walkPersonOn'])}
            // Spelled out, or the face, the name and the number run together
            // into "Ada4".
            aria-label={`${person.name}, ${person.cards} ${person.cards === 1 ? 'card' : 'cards'}`}
            aria-pressed={person.name === current}
            onClick={() => onPick(person.name)}
          >
            <Avatar name={person.name} emoji={person.avatar} size={26} />
            <span className={styles['walkName']}>{person.name}</span>
            <span className={styles['walkCount']}>{person.cards}</span>
          </button>
        ))}
      </div>

      <Button disabled={!current} onClick={onExit}>
        Show everyone
      </Button>
    </div>
  );
}
