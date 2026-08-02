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
// Codegen'd from the server's own tuple. A hand-written copy here would be a
// third list agreeing with nothing, and the server rejects what it does not
// recognise — so a picker built from anything else offers dead options.
import { AVATARS } from '../../types/enums';
import { formatOp, formatPath } from './paths';
import styles from './history.module.css';
import type { EditPerson, EditRow } from './state';

export interface HistoryProps {
  rows: readonly EditRow[];
  people: readonly EditPerson[];
  /** A path to filter to, or null for everything. */
  filter: string | null;
  onFilter(path: string | null): void;
  name: string;
  avatar: string;
  editable: boolean;
  onIdentity(name: string, avatar: string): void;
  onRevert(id: string): void;
  onFocusPath(path: string): void;
}

export function History({
  rows,
  people,
  filter,
  onFilter,
  name,
  avatar,
  editable,
  onIdentity,
  onRevert,
  onFocusPath,
}: HistoryProps) {
  const [open, setOpen] = useState(false);
  const shown = filter ? rows.filter((row) => row.path === filter) : rows;
  const ordered = [...shown].reverse();

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
          <p className={styles['caveat']}>
            Names are self-declared. Anyone with this link can edit, and can claim any name.
          </p>

          <Identity name={name} avatar={avatar} editable={editable} onIdentity={onIdentity} />

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
                <li key={row.id} className={styles['row']}>
                  <p className={styles['what']}>
                    <span aria-hidden="true">{row.avatar || '🙂'} </span>
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
                  <p className={styles['meta']}>
                    <span>{row.at.slice(0, 10)}</span>
                    {editable && row.op !== 'revert' ? (
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

function Identity({
  name,
  avatar,
  editable,
  onIdentity,
}: {
  name: string;
  avatar: string;
  editable: boolean;
  onIdentity(name: string, avatar: string): void;
}) {
  const [draft, setDraft] = useState(name);
  if (!editable) return <p className={styles['closed']}>Editing is closed for this document.</p>;

  return (
    <div className={styles['identity']}>
      <label className={styles['label']} htmlFor="editor-name">
        {name ? 'Editing as' : 'Say who you are to start editing'}
      </label>
      <div className={styles['identityRow']}>
        <input
          id="editor-name"
          className={styles['nameInput']}
          value={draft}
          placeholder="Your name"
          onInput={(event) => setDraft((event.target as HTMLInputElement).value)}
          onBlur={() => onIdentity(draft.trim(), avatar)}
          onKeyDown={(event: KeyboardEvent) => {
            if (event.key === 'Enter') onIdentity(draft.trim(), avatar);
          }}
        />
        <span className={styles['avatars']}>
          {AVATARS.map((option) => (
            <button
              key={option}
              type="button"
              className={styles['avatarPick']}
              aria-label={`Use the ${option} avatar`}
              aria-pressed={option === avatar}
              onClick={() => onIdentity(draft.trim() || name, option)}
            >
              {option}
            </button>
          ))}
        </span>
      </div>
    </div>
  );
}
