/**
 * Where a card is written: in the column it is going to live in.
 *
 * ## One composer per column, again
 *
 * This replaces a single composer pinned below the whole board, whose
 * destination was a segmented radiogroup — pick "Demos", type, press Add. That
 * was a deliberate consolidation of four textareas into one, and it was wrong
 * for a reason no amount of labelling fixes: *where you type* and *where the
 * card lands* were different places on the screen, so choosing a column became
 * a thing you had to read rather than a thing you did. Reported plainly as "a
 * card should just be added within the column and not a separate text box".
 *
 * So the destination is expressed the way it always should have been — by
 * which box you are typing in — and the segmented control disappears with it.
 *
 * ## Why it is the column's footer, not a draft card in the list
 *
 * `.cards` scrolls. A composer inside it would be at the bottom of a scroll
 * box, so on a full column you would have to scroll to reach the control you
 * were reaching for. As a sibling of `.cards` it is always visible, and cards
 * land directly above it (`board.add_card` appends), which is where you are
 * already looking. On a phone one column fills the screen, so the footer is
 * also the thumb zone the pinned bar was there to occupy.
 *
 * The draft lives here in `useState`, like {@link CardEditor}'s: nothing the
 * server sends can reach it, so a snapshot landing mid-sentence cannot disturb
 * what you have typed.
 */

import { useEffect, useRef, useState } from 'react';

import { Button } from '../shared';
import styles from './retro.module.css';

export interface ColumnComposerProps {
  /** The column's human label, for the accessible names. */
  label: string;
  /** Expanded, i.e. showing the textarea rather than the ghost button. */
  open: boolean;
  /**
   * Bumped to pull focus into the box, including when it is already open.
   *
   * A boolean will not do: re-opening the column you are already composing in
   * must still put the caret back, and a flag that is already true does not
   * re-run an effect.
   */
  focusNonce: number;
  onOpen(): void;
  onClose(): void;
  onSubmit(text: string): void;
  /** Fired as you type, to drive the column's "someone is writing" ghost. */
  onTyping(): void;
}

export function ColumnComposer({
  label,
  open,
  focusNonce,
  onOpen,
  onClose,
  onSubmit,
  onTyping,
}: ColumnComposerProps) {
  const [text, setText] = useState('');
  const boxRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (open && focusNonce > 0) boxRef.current?.focus();
  }, [open, focusNonce]);

  const submit = (): void => {
    if (!text.trim()) return;
    onSubmit(text);
    setText('');
    // Stays open and focused: a retro is written in bursts of three or four
    // cards, and re-opening the box between each one is the whole cost.
    boxRef.current?.focus();
  };

  if (!open) {
    return (
      <div className={styles['columnCompose']}>
        <button type="button" className={styles['composeGhost']} aria-label={`Add a card to ${label}`} onClick={onOpen}>
          <span aria-hidden="true">＋</span> Add a card
        </button>
      </div>
    );
  }

  return (
    <div className={styles['columnCompose']}>
      <textarea
        ref={boxRef}
        className={styles['composeBox']}
        rows={2}
        value={text}
        placeholder={`Add to ${label}…`}
        aria-label={`Add a card to ${label}`}
        onInput={(event) => {
          setText((event.target as HTMLTextAreaElement).value);
          onTyping();
        }}
        onKeyDown={(event) => {
          // ⌘/Ctrl-Enter, not bare Enter: cards are frequently multi-line and
          // a bare Enter that submitted would make writing a second line
          // impossible.
          if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
            event.preventDefault();
            submit();
          } else if (event.key === 'Escape') {
            // Stopped, or this also exits a walkthrough — App binds Escape at
            // the document while one is running.
            event.stopPropagation();
            setText('');
            onClose();
          }
        }}
      />
      <div className={styles['composeActions']}>
        <Button
          onClick={() => {
            setText('');
            onClose();
          }}
        >
          Cancel
        </Button>
        <Button tone="primary" disabled={!text.trim()} onClick={submit}>
          Add
        </Button>
      </div>
    </div>
  );
}
