/**
 * A card's reactions: the counted ones as chips, the rest behind a picker.
 *
 * Split into three pieces, because putting them together produced the single
 * ugliest thing on the board. The chips row was rendered under every card, and
 * it contained the "add a reaction" trigger — so a card with no reactions, which
 * is most cards, still showed a lone 🙂 floating in empty space below the
 * author line. It read as a rendering fault rather than a control.
 *
 * Now {@link ReactionChips} renders nothing at all when there is nothing to
 * count, and {@link ReactionTrigger} sits in the card's action row with edit,
 * delete and the drag grip — which is where a control belongs and where the eye
 * already goes looking for one.
 *
 * Showing only emoji that already have a count is what keeps cards readable —
 * sixteen zero-count chips per card would be more chrome than content. The tray
 * is a `role="menu"`, so a screen reader announces "menu, 16 items" rather than
 * sixteen loose buttons appearing from nowhere.
 *
 * ## Why {@link ReactionTray} is a portal
 *
 * `.cards` is `overflow-y: auto`, so each column is a scroll box — and a
 * non-`visible` overflow on one axis forces the other to `auto` too. A panel
 * positioned inside the card is clipped on both, which in a four-column layout
 * showed the last two emoji of sixteen. It renders on `document.body` and is
 * placed from the trigger's rect, flipping against whichever edge it would
 * otherwise run off.
 *
 * The trigger and the tray are still two components: the card owns the open
 * state, because it is also what closes the tray on a click elsewhere.
 */

import { useLayoutEffect, useState, type RefObject } from 'react';
import { createPortal } from 'react-dom';

import { Icon } from '../design/primitives';
import { cx } from '../runtime/cx';
import { REACTION_EMOJIS } from '../types/enums';
import { Button } from '../shared';
import styles from './retro.module.css';

export interface ReactionChipsProps {
  /** emoji → count, from the snapshot. Zero-count entries are not rendered. */
  reactions: Record<string, number>;
  /** Emoji this browser has reacted with, for the "mine" highlight. */
  mine: ReadonlySet<string>;
  onReact(emoji: string): void;
  /** The board is locked by the host — reactions are frozen with everything else. */
  disabled?: boolean;
}

export function ReactionChips({ reactions, mine, onReact, disabled }: ReactionChipsProps) {
  const counted = REACTION_EMOJIS.filter((emoji) => (reactions[emoji] ?? 0) > 0);
  // The whole row, not just its contents. An empty flex row still occupies its
  // gap and padding, which is what left a strip of dead space under every card.
  if (counted.length === 0) return null;

  return (
    <div className={styles['reactions']}>
      {counted.map((emoji) => {
        const isMine = mine.has(emoji);
        return (
          <button
            key={emoji}
            type="button"
            className={cx(styles['rxChip'], isMine && styles['rxChipMine'])}
            disabled={disabled}
            // "Remove"/"Add" rather than a bare emoji: a toggle whose label does
            // not say which way it toggles is a coin flip for anyone who cannot
            // see the highlight that distinguishes the two states.
            aria-label={`${isMine ? 'Remove' : 'Add'} ${emoji} reaction (${reactions[emoji]})`}
            aria-pressed={isMine}
            onClick={() => onReact(emoji)}
          >
            <span aria-hidden="true" className={styles['rxGlyph']}>
              {emoji}
            </span>
            <span className={styles['rxCount']}>{reactions[emoji]}</span>
          </button>
        );
      })}
    </div>
  );
}

export interface ReactionTriggerProps {
  open: boolean;
  onToggle(): void;
  /** Id of the tray, for `aria-controls`. */
  trayId: string;
  /** The card holds this, to put focus back after Escape closes the tray. */
  buttonRef: RefObject<HTMLButtonElement>;
  disabled?: boolean;
}

export function ReactionTrigger({ open, onToggle, trayId, buttonRef, disabled }: ReactionTriggerProps) {
  return (
    <Button
      shape="bare"
      // `emphasis`, not `active`: `aria-expanded` below already carries the
      // open state, and a control that is both pressed and expanded announces
      // the same fact twice.
      emphasis={open}
      ref={buttonRef}
      disabled={disabled}
      aria-label="Add a reaction"
      aria-haspopup="menu"
      aria-expanded={open}
      aria-controls={trayId}
      onClick={onToggle}
    >
      <Icon name="smile" size={16} />
    </Button>
  );
}

export interface ReactionTrayProps {
  id: string;
  mine: ReadonlySet<string>;
  onPick(emoji: string): void;
  /** The trigger the tray hangs off. */
  anchorRef: RefObject<HTMLButtonElement | null>;
  /** Taken by the tray element, so the card can count it as inside itself. */
  trayRef: RefObject<HTMLDivElement | null>;
}

/** The tray's own box, in px. Placement needs it before it exists. */
const TRAY_W = 268;
const TRAY_H = 96;

export function ReactionTray({ id, mine, onPick, anchorRef, trayRef }: ReactionTrayProps) {
  const [at, setAt] = useState<{ top: number; left: number } | null>(null);

  // Layout, not effect: measured and placed before the browser paints, or the
  // tray shows for one frame in the top-left corner.
  useLayoutEffect(() => {
    const anchor = anchorRef.current;
    if (!anchor) return;
    const rect = anchor.getBoundingClientRect();
    const gap = 8;
    const width = Math.min(TRAY_W, window.innerWidth - gap * 2);
    // Right-aligned on the trigger, then pushed back inside whichever edge it
    // would cross. A card in the last column opens leftward for free this way.
    const left = Math.max(gap, Math.min(rect.right - width, window.innerWidth - width - gap));
    const below = window.innerHeight - rect.bottom;
    const top = below < TRAY_H + gap ? rect.top - TRAY_H - gap : rect.bottom + gap;
    setAt({ top: Math.max(gap, top), left });
  }, [anchorRef]);

  return createPortal(
    <div
      ref={trayRef as RefObject<HTMLDivElement>}
      id={id}
      className={styles['rxTray']}
      role="menu"
      aria-label="Reactions"
      style={at ? { top: `${at.top}px`, left: `${at.left}px`, width: `${TRAY_W}px` } : { opacity: 0 }}
    >
      {REACTION_EMOJIS.map((emoji) => (
        <button
          key={emoji}
          type="button"
          role="menuitem"
          className={cx(styles['rxPick'], mine.has(emoji) && styles['rxChipMine'])}
          aria-label={emoji}
          onClick={() => onPick(emoji)}
        >
          <span aria-hidden="true">{emoji}</span>
        </button>
      ))}
    </div>,
    document.body
  );
}
