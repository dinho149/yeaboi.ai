/**
 * The way in.
 *
 * Every affordance in this stack is gated on having said who you are: the
 * pencils, the add rows and the note slots all render nothing until `enabled`,
 * because an unattributed correction helps nobody read the history later. That
 * gate was correct and its *entrance* was hidden — the only place to type a
 * name was inside a collapsed panel at the very bottom of the document, behind
 * a button labelled "Edits", which reads as a record of the past rather than an
 * invitation. A reader who did not already know the feature existed saw a
 * perfectly ordinary read-only report.
 *
 * So the invitation goes at the top, where reading starts, and it says the
 * three things a reader needs before they will touch someone else's document:
 * that they may, what happens to what they write, and who will be able to see
 * that it was them.
 *
 * **The honesty line is not decoration.** Every name here was typed by whoever
 * held the link, into their own browser. It sits next to the field where that
 * happens, which is the one place it cannot be missed — and the history panel
 * repeats it, because that is the other surface that shows names.
 */

import { useState, type RefObject } from 'react';

import { Button } from '../../shared/Button';
// Codegen'd from the server's own tuple, for the same reason the history panel
// uses it: the server rejects an avatar it does not recognise, so a picker built
// from a hand-written copy offers dead options.
import { AVATARS } from '../../types/enums';
import styles from './editbar.module.css';

/** Repeated verbatim in the history panel — both surfaces render a name. */
export const SELF_DECLARED = 'Names are self-declared: anyone with this link can edit, and can claim any name.';

export interface EditBarProps {
  /** False once the host has closed editing. */
  editable: boolean;
  name: string;
  avatar: string;
  /** How many corrections the document already carries. */
  count: number;
  onIdentity(name: string, avatar: string): void;
  /** Focused when the history dock's "Correct this" is used from further down. */
  inputRef: RefObject<HTMLInputElement>;
}

export function EditBar({ editable, name, avatar, count, onIdentity, inputRef }: EditBarProps) {
  const [changing, setChanging] = useState(false);
  const [draft, setDraft] = useState(name);

  if (!editable) {
    return (
      <p className={styles['closed']}>
        Editing is closed for this document.
        {count ? ` ${count} ${count === 1 ? 'correction was' : 'corrections were'} made.` : ''}
      </p>
    );
  }

  if (name && !changing) {
    return (
      <div className={styles['bar']} data-state="editing">
        <p className={styles['text']}>
          <span className={styles['title']}>
            {/* The space is inside the expression: JSX strips trailing
                whitespace before a newline, so `{avatar} ` on its own line
                renders the emoji flush against the next word. */}
            <span aria-hidden="true">{`${avatar} `}</span>
            Editing as {name}
          </span>
          <span className={styles['hint']}>
            Use the <b>✎</b> beside anything underlined to correct it, or <b>＋ Add</b> to append something new.
          </span>
        </p>
        <span className={styles['controls']}>
          <Button
            size="s"
            shape="bare"
            onClick={() => {
              setDraft(name);
              setChanging(true);
            }}
          >
            Change name
          </Button>
          {/* Clearing the name is what turns the affordances back off, so this
              really is "stop editing" and not just a cosmetic toggle. */}
          <Button size="s" shape="bare" onClick={() => onIdentity('', avatar)}>
            Done
          </Button>
        </span>
      </div>
    );
  }

  const start = () => {
    const trimmed = draft.trim();
    if (!trimmed) return;
    onIdentity(trimmed, avatar);
    setChanging(false);
  };

  return (
    <div className={styles['bar']} data-state="inviting">
      <p className={styles['text']}>
        <span className={styles['title']}>Something wrong? You can fix it.</span>
        <span className={styles['hint']}>
          This document can be corrected in place. Every change is kept, shown against your name, and can be undone.
        </span>
      </p>

      <span className={styles['controls']}>
        <label className={styles['srOnly']} htmlFor="editor-name">
          Your name
        </label>
        <input
          id="editor-name"
          ref={inputRef}
          className={styles['nameInput']}
          value={draft}
          placeholder="Your name"
          autoComplete="off"
          onInput={(event) => setDraft((event.target as HTMLInputElement).value)}
          onKeyDown={(event: KeyboardEvent) => {
            if (event.key === 'Enter') {
              event.preventDefault();
              start();
            }
          }}
        />
        <Button onClick={start} disabled={!draft.trim()} tone="primary" size="s">
          Start editing
        </Button>
      </span>

      <span className={styles['avatars']} role="group" aria-label="Pick an avatar">
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

      <p className={styles['caveat']}>{SELF_DECLARED}</p>
    </div>
  );
}
