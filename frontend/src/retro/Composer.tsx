/**
 * The single composer, pinned to the bottom of the screen.
 *
 * ## One, not four
 *
 * Every column used to carry its own `<textarea>` and Add button — four copies
 * of the same control, and on a phone the one you wanted was always the one off
 * the bottom of a column you had to scroll to reach. A segmented control picks
 * the destination instead, so the write surface is in the same place whatever
 * you are writing, and it sits in the thumb zone. It also mirrors poker's fixed
 * deck strip, which is what makes the two boards read as one product rather
 * than two apps that happen to share a colour scheme.
 *
 * The segmented control is a real `radiogroup`: arrow keys move between the
 * columns, and the current destination is announced. A row of buttons with a
 * `.sel` class — what both boards do today — conveys none of that.
 */

import { useEffect, useRef } from 'react';

import { cx } from '../runtime/cx';
import { RETRO_GRID_LABELS, RETRO_GRIDS, type RetroGrids } from '../types/enums';
import styles from './retro.module.css';

export interface ComposerProps {
  grid: RetroGrids;
  onGridChange(grid: RetroGrids): void;
  text: string;
  onTextChange(text: string): void;
  onSubmit(): void;
  locked: boolean;
  /** Bumped by the column `+` buttons to pull focus into the textarea. */
  focusNonce: number;
}

export function Composer({
  grid,
  onGridChange,
  text,
  onTextChange,
  onSubmit,
  locked,
  focusNonce,
}: ComposerProps) {
  const boxRef = useRef<HTMLTextAreaElement | null>(null);

  // A nonce rather than a boolean: pressing `+` on the column you are already
  // composing into must still pull focus back, and a boolean that is already
  // true does not re-run an effect.
  useEffect(() => {
    if (focusNonce > 0) boxRef.current?.focus();
  }, [focusNonce]);

  const move = (delta: number): void => {
    const next = RETRO_GRIDS[(RETRO_GRIDS.indexOf(grid) + delta + RETRO_GRIDS.length) % RETRO_GRIDS.length];
    if (next) onGridChange(next);
  };

  if (locked) {
    return (
      <div className={styles['composer']}>
        <p className={styles['composerLocked']} role="status">
          <span aria-hidden="true">🔒</span> The host has locked the board.
        </p>
      </div>
    );
  }

  return (
    <div className={styles['composer']}>
      <div className={styles['segmented']} role="radiogroup" aria-label="Add to column">
        {RETRO_GRIDS.map((option) => {
          const selected = option === grid;
          return (
            <button
              key={option}
              type="button"
              role="radio"
              aria-checked={selected}
              tabIndex={selected ? 0 : -1}
              className={cx(styles['segment'], selected && styles['segmentOn'])}
              onClick={() => onGridChange(option)}
              onKeyDown={(event) => {
                if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
                  event.preventDefault();
                  move(1);
                } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
                  event.preventDefault();
                  move(-1);
                }
              }}
            >
              {RETRO_GRID_LABELS[option]}
            </button>
          );
        })}
      </div>

      <div className={styles['composerRow']}>
        <textarea
          ref={boxRef}
          className={styles['composerBox']}
          rows={1}
          value={text}
          placeholder={`Add to ${RETRO_GRID_LABELS[grid]}…`}
          aria-label={`Add a card to ${RETRO_GRID_LABELS[grid]}`}
          onInput={(event) => onTextChange((event.target as HTMLTextAreaElement).value)}
          onKeyDown={(event) => {
            // ⌘/Ctrl-Enter, not bare Enter: cards are frequently multi-line and
            // a bare Enter that submitted would make writing a second line
            // impossible.
            if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
              event.preventDefault();
              onSubmit();
            }
          }}
        />
        <button
          type="button"
          className={styles['addBtn']}
          disabled={!text.trim()}
          onClick={onSubmit}
        >
          Add
        </button>
      </div>
    </div>
  );
}
