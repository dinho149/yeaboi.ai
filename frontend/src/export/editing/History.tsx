/**
 * Who changed what, and the way back.
 *
 * A single panel anchored to the corner of the document rather than a marker
 * beside every edit: the question people actually ask is "what did the team
 * change since I last read this", which is a list, not a diff.
 *
 * The honesty line at the top is not decoration. Every name here was typed by
 * whoever held the link, into their own browser. Drawing a verified badge, a
 * lock, or calling this an audit log would each be a claim the system cannot
 * make — the same trust model the retro board has always had, said out loud.
 */

import { useState } from 'react';

import { Button } from '../../shared/Button';
import { SELF_DECLARED } from './EditBar';
import { formatOp, formatPath } from './paths';
import styles from './history.module.css';
import type { EditPerson, EditRow } from './state';

export interface HistoryProps {
  rows: readonly EditRow[];
  people: readonly EditPerson[];
  /** A path to filter to, or null for everything. */
  filter: string | null;
  onFilter(path: string | null): void;
  /** Empty until this reader has named themselves in the bar at the top. */
  name: string;
  editable: boolean;
  /**
   * Send an un-named reader back to the bar. The dock is reachable from
   * anywhere in a long document and the invitation is not, so this is the
   * entrance for someone who scrolled first and decided to correct something
   * second.
   */
  onWantToEdit(): void;
  onRevert(id: string): void;
  onFocusPath(path: string): void;
}

export function History({
  rows,
  people,
  filter,
  onFilter,
  name,
  editable,
  onWantToEdit,
  onRevert,
  onFocusPath,
}: HistoryProps) {
  const [open, setOpen] = useState(false);
  const shown = filter ? rows.filter((row) => row.path === filter) : rows;
  const ordered = [...shown].reverse();

  // Nothing to dock: a closed document with no history has neither a record to
  // show nor an invitation to make.
  if (!editable && !rows.length) return null;

  if (editable && !name) {
    return (
      <div className={styles['dock']}>
        <Button onClick={onWantToEdit} size="s" tone="primary">
          ✎ Correct this{rows.length ? ` · ${rows.length}` : ''}
        </Button>
      </div>
    );
  }

  return (
    <div className={styles['dock']}>
      {people.length ? (
        <span className={styles['people']}>
          {people.map((person, index) => (
            <span key={`${person.name}-${index}`} title={person.name}>
              {person.avatar || '🙂'}
            </span>
          ))}
        </span>
      ) : null}

      <Button onClick={() => setOpen((was) => !was)} size="s" aria-expanded={open || Boolean(filter)}>
        ✎ Edits ({rows.length})
      </Button>

      {open || filter ? (
        <div className={styles['panel']}>
          <p className={styles['caveat']}>{SELF_DECLARED}</p>

          {filter ? (
            <p className={styles['filter']}>
              Showing <strong>{formatPath(filter)}</strong>{' '}
              <button type="button" className={styles['clear']} onClick={() => onFilter(null)}>
                show all
              </button>
            </p>
          ) : null}

          {!ordered.length ? (
            <p className={styles['empty']}>No changes yet.</p>
          ) : (
            <ul className={styles['list']}>
              {ordered.map((row) => (
                <li key={row.id} className={styles['row']} data-unapplied={row.applied ? undefined : '1'}>
                  <p className={styles['what']}>
                    {/* Space inside the expression — JSX strips it before a
                        newline, which runs the emoji into the name. */}
                    <span aria-hidden="true">{`${row.avatar || '🙂'} `}</span>
                    <strong>{row.author || 'Someone'}</strong> {formatOp(row.op)}{' '}
                    <button
                      type="button"
                      className={styles['where']}
                      onClick={() => {
                        onFilter(row.path);
                        onFocusPath(row.path);
                      }}
                    >
                      {formatPath(row.path)}
                    </button>
                  </p>
                  {row.value ? <p className={styles['value']}>{row.value}</p> : null}
                  {/* Said in words, not signalled by the dimming alone: this
                      correction was made and the artifact can no longer take
                      it, which is a different thing from it never happening. */}
                  {row.applied ? null : (
                    <p className={styles['unapplied']}>
                      Could not be applied{row.reason ? ` — ${row.reason}` : ''}
                    </p>
                  )}
                  <p className={styles['meta']}>
                    <span>{row.at.slice(0, 10)}</span>
                    {editable && row.applied && row.op !== 'revert' ? (
                      <button type="button" className={styles['revert']} onClick={() => onRevert(row.id)}>
                        Revert
                      </button>
                    ) : null}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}
    </div>
  );
}
