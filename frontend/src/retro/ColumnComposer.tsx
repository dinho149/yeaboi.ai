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

import { useEffect, useLayoutEffect, useRef, useState } from 'react';

import { Icon } from '../design/primitives';
import { cx } from '../runtime/cx';
import styles from './retro.module.css';

/** How long the draft takes to fold away. Matches `draftOut` in the sheet. */
const DRAFT_OUT_MS = 160;

export interface ColumnComposerProps {
  /** The column's human label, for the accessible names. */
  label: string;
  /** Expanded, i.e. showing the textarea rather than the empty strip. */
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
  // Held open for the length of the exit. A box that is unmounted the moment it
  // closes has nothing left to animate — the draft would simply vanish.
  const [leaving, setLeaving] = useState(false);
  const boxRef = useRef<HTMLTextAreaElement>(null);
  const wasOpen = useRef(open);

  useEffect(() => {
    if (open && focusNonce > 0) boxRef.current?.focus();
  }, [open, focusNonce]);

  // Layout, not effect: an ordinary effect runs after paint, so the render that
  // closed the box would show one frame with it already gone and the next with
  // it back and folding — which is the flicker, not the fold.
  useLayoutEffect(() => {
    const was = wasOpen.current;
    wasOpen.current = open;
    if (open || !was) return undefined;
    setLeaving(true);
    const timer = window.setTimeout(() => setLeaving(false), DRAFT_OUT_MS);
    return () => window.clearTimeout(timer);
  }, [open]);

  const submit = (): void => {
    if (!text.trim()) return;
    onSubmit(text);
    setText('');
    // Stays open and focused: a retro is written in bursts of three or four
    // cards, and re-opening the box between each one is the whole cost.
    boxRef.current?.focus();
  };

  return (
    <div className={styles['composeSlot']}>
      {/* The rest of the column, as one control. Nothing is drawn until the
          pointer is over it, so the board is cards and nothing else at rest.
          It stays put underneath the box, which is what the box folds back
          into — and what is clickable again the moment it starts to. */}
      {open ? null : (
        <button
          type="button"
          className={styles['composeArea']}
          aria-label={`Add a card to ${label}`}
          onClick={onOpen}
        >
          <span className={styles['composeHint']}>
            <Icon name="plus" size={14} /> Add a card
          </span>
        </button>
      )}

      {open || leaving ? (
        // On the way out it is a picture of itself: out of the accessibility
        // tree and out of reach, because a box that has been dismissed must not
        // still be announced or tabbed into while it folds away.
        <div
          className={cx(styles['columnCompose'], !open && styles['columnComposeOut'])}
          {...(open ? {} : { 'aria-hidden': true, inert: true })}
        >
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
              // Enter posts, Shift-Enter is the second line. Same pair as the
              // card editor, so writing a card and changing one are one habit.
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                submit();
              } else if (event.key === 'Escape') {
                // Stopped, or this also exits a walkthrough — App binds Escape
                // at the document while one is running.
                event.stopPropagation();
                setText('');
                onClose();
              }
            }}
          />
        </div>
      ) : null}
    </div>
  );
}
