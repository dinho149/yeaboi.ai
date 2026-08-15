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

import { Eyebrow } from '../design/primitives';
import type { PokerVote } from '../types/board';
import styles from './poker.module.css';

export interface TableProps {
  /** One entry per person present. `value` exists only once revealed. */
  votes: readonly PokerVote[];
  revealed: boolean;
}

export function Table({ votes, revealed }: TableProps) {
  const waiting = votes.filter((vote) => !vote.voted).length;

  return (
    <section className={styles['table']} aria-label="The table">
      <div className={styles['tableHead']}>
        {/* The head carries one number, never two: while voting that is the
            outstanding count, and only once revealed is it the headcount. */}
        <Eyebrow value={revealed ? votes.length || undefined : undefined}>The table</Eyebrow>
        {!revealed && votes.length ? (
          <span className={styles['tableWait']}>
            {waiting === 0 ? 'everyone is in' : `${waiting} still to vote`}
          </span>
        ) : null}
      </div>

      {votes.length === 0 ? (
        <p className={styles['vempty']}>
          {revealed ? 'No votes were cast.' : 'Waiting for the team — share the code to invite them.'}
        </p>
      ) : (
        <ul className={styles['vrow']}>
          {votes.map((person, index) => (
            <li key={`${person.name}:${index}`} className={styles['voter']}>
              {revealed ? (
                <span className={styles['vcard']} style={{ '--i': index } as never}>
                  {person.value}
                </span>
              ) : (
                <span className={styles['face']}>
                  <span aria-hidden="true">{person.avatar || '🙂'}</span>
                  {person.voted ? (
                    <span className={styles['tick']} aria-hidden="true">
                      ✓
                    </span>
                  ) : null}
                </span>
              )}
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
