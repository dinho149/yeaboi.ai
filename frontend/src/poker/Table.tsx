/**
 * The seats at the table.
 *
 * Two renderings of the same row, because pre- and post-reveal are different
 * questions. While voting the only fact is *whether* someone has voted, so the
 * seat is a face with a tick — the value is not on the wire at all, which is
 * where vote secrecy is actually enforced. On reveal it becomes the value.
 *
 * The reveal is the moment poker was missing. It used to be a silent swap: the
 * numbers were simply there on the next poll, with nothing to look at and
 * nothing announced. Now the cards turn over around the table — `--i` staggers
 * each seat by its index, so it reads as one motion sweeping the table rather
 * than ten simultaneous flips — and the App announces it to assistive tech.
 */

import { useRef } from 'react';

import { Icon } from '../design/primitives';
import type { PokerVote } from '../types/board';
import { useRowChoreography } from '../shared/board';
import styles from './poker.module.css';

export interface TableProps {
  /** One entry per person present. `value` exists only once revealed. */
  votes: readonly PokerVote[];
  revealed: boolean;
  /**
   * The two people arguing, while a floor is open. Everyone else is watching,
   * and gets a bucket of popcorn to say so. Empty the rest of the time.
   *
   * By name, because that is the only thing a seat and a duelist have in
   * common on the wire — a seat carries no participant id.
   */
  arguing?: readonly string[] | undefined;
}

export function Table({ votes, revealed, arguing = [] }: TableProps) {
  const onTheFloor = new Set(arguing);
  // A chair is empty the moment its occupant is called up. Nothing is held back
  // for the sake of the animation — the floor flies them out of the rectangle
  // this table recorded on its last layout, not out of the element.
  const seated = votes.filter((person) => !onTheFloor.has(person.name));
  const row = useRef<HTMLUListElement>(null);
  // Seats slide to the places the row redistributes to, and the two coming back
  // fly out of the floor panel they were just in.
  useRowChoreography(row, { place: 'table', from: 'floor', members: seated.map((p) => p.name).join(' ') });

  return (
    <section className={styles['table']} aria-label="The table">
      {/* No label and no status: the row of faces is unmistakably the table,
          the section carries its name for anything that cannot see them, and
          who the round is waiting for is said on the deck's own line. */}

      {seated.length === 0 ? (
        <p className={styles['vempty']}>
          {revealed ? 'No votes were cast.' : 'Waiting for the team — share the code to invite them.'}
        </p>
      ) : (
        <ul className={styles['vrow']} ref={row}>
          {seated.map((person, index) => (
            // Keyed by name, not by index: a seat leaving for the floor must
            // take its own element with it rather than handing it to the person
            // who moves up into its place, or the table swaps two faces over.
            <li key={person.name} className={styles['voter']} data-travel={person.name}>
              {/* The seat does not change on reveal — the vote arrives beside
                  the name as a card, so the table stays the same table. */}
              <span className={styles['seatFace']}>
                <span className={styles['face']} data-face="">
                  <span aria-hidden="true">{person.avatar || <Icon name="user" size={16} />}</span>
                  {!revealed && person.voted ? (
                    <span className={styles['tick']} aria-hidden="true">
                      <Icon name="check" size={11} strokeWidth={3} />
                    </span>
                  ) : null}
                  {/* Same corner as the tick, which is free by then: the tick
                      is a voting-phase mark and the floor only opens after. */}
                  {arguing.length && !arguing.includes(person.name) ? (
                    <span className={styles['popcorn']} aria-hidden="true">
                      🍿
                    </span>
                  ) : null}
                </span>
                {revealed ? (
                  <span className={styles['vcard']} style={{ '--i': index } as never} aria-hidden="true">
                    {person.value}
                  </span>
                ) : null}
              </span>
              <span className={styles['nm']} title={person.name}>
                {person.name}
              </span>
              {/* The seat's meaning, spelled out once per person: the tick and
                  the flipped card are both purely visual. */}
              <span className={styles['srOnly']}>
                {revealed ? `voted ${person.value}` : person.voted ? 'has voted' : 'has not voted yet'}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
