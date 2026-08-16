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

import { useLayoutEffect, useRef } from 'react';

import { Icon } from '../design/primitives';
import type { PokerVote } from '../types/board';
import { floorFace, rememberSeat, type FaceBox } from './seats';
import styles from './poker.module.css';

/** How long a seat takes to slide to its new place, or back from the floor. */
const MOVE_MS = 420;
const MOVE_EASE = 'cubic-bezier(0.32, 0.94, 0.3, 1)';

/**
 * The table rearranges itself rather than jumping.
 *
 * Two people leaving for the floor closes a gap the row then has to redistribute,
 * and every remaining seat lands somewhere new. Done by the browser alone that is
 * an instant teleport in the middle of a movement the floor is making smoothly —
 * so each seat is animated from where it was to where it now is, and the two
 * coming back from the floor are animated from the panel they were just in.
 *
 * Keyed on who is seated, not on every poll: the board re-renders once a second
 * and measuring ten elements each time would force a layout for nothing.
 */
function useSeatChoreography(row: { current: HTMLUListElement | null }, seatKey: string): void {
  const was = useRef(new Map<string, FaceBox>());

  useLayoutEffect(() => {
    const list = row.current;
    if (!list) return;
    const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const now = new Map<string, FaceBox>();

    for (const seat of list.querySelectorAll<HTMLElement>('[data-seat]')) {
      const name = seat.dataset['seat'] ?? '';
      const face = seat.querySelector('[data-face]');
      if (!face) continue;
      const here = seat.getBoundingClientRect();
      const centre = { x: here.left + here.width / 2, y: here.top + here.height / 2, width: here.width };
      // Back from the floor, if that is where they were; otherwise from
      // wherever this seat sat before the row closed up.
      const from = was.current.get(name) ?? floorFace(name);
      now.set(name, centre);
      rememberSeat(name, face);

      if (still || !from || typeof seat.animate !== 'function') continue;
      const dx = from.x - centre.x;
      const dy = from.y - centre.y;
      if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5) continue;
      const scale = was.current.has(name) ? 1 : Math.min(2.5, Math.max(0.4, from.width / (centre.width || 1)));
      seat.animate(
        [
          { transform: `translate(${dx}px, ${dy}px) scale(${scale})`, opacity: was.current.has(name) ? 1 : 0 },
          { transform: 'none', opacity: 1 },
        ],
        { duration: MOVE_MS, easing: MOVE_EASE }
      );
    }

    was.current = now;
  }, [row, seatKey]);
}

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
  useSeatChoreography(row, seated.map((person) => person.name).join(' '));

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
            <li key={person.name} className={styles['voter']} data-seat={person.name}>
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
