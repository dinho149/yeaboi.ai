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
 * ## Why {@link ReactionTray} is in the card's flow, not floating over it
 *
 * It used to be `position: absolute`, hanging above the trigger. That looked
 * right in isolation and was broken on the board: `.cards` is `overflow-y: auto`
 * so each column is a scroll box, and a non-`visible` overflow on one axis
 * forces the other to `auto` too — so the column clipped the panel on both
 * axes. In a four-column layout the ~236px panel was wider than its ~250px
 * column allowed for once right-anchored, and all you saw was the last couple
 * of emoji. It was reported, reasonably, as "there are only 2 emojis".
 *
 * A tray in the flow simply cannot be clipped: it pushes the card taller, and
 * the column scrolls to it like any other content. The alternatives were a
 * portal with `getBoundingClientRect` (which is what the pre-React code did,
 * and is only worth its cost when a re-render would otherwise destroy the
 * panel — nothing does that here) or the top-layer `popover` attribute, which
 * the older phones a tunnel link gets opened on do not have.
 *
 * The cost is that the trigger and the tray are no longer one component: the
 * trigger belongs in the action row and the tray belongs below it, so the card
 * owns the open state and renders the two in their two places.
 */

import type { RefObject } from 'react';

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
            <span aria-hidden="true">{emoji}</span>
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
      active={open}
      ref={buttonRef}
      disabled={disabled}
      aria-label="Add a reaction"
      aria-haspopup="menu"
      aria-expanded={open}
      aria-controls={trayId}
      onClick={onToggle}
    >
      <span aria-hidden="true">☺</span>
    </Button>
  );
}

export interface ReactionTrayProps {
  id: string;
  mine: ReadonlySet<string>;
  onPick(emoji: string): void;
}

export function ReactionTray({ id, mine, onPick }: ReactionTrayProps) {
  return (
    <div id={id} className={styles['rxTray']} role="menu" aria-label="Reactions">
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
    </div>
  );
}
