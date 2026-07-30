/**
 * A card's reactions: the counted ones as chips, the rest behind a picker.
 *
 * Split into two pieces, because putting them together produced the single
 * ugliest thing on the board. The chips row was rendered under every card, and
 * it contained the "add a reaction" trigger — so a card with no reactions, which
 * is most cards, still showed a lone 🙂 floating in empty space below the
 * author line. It read as a rendering fault rather than a control.
 *
 * Now {@link ReactionChips} renders nothing at all when there is nothing to
 * count, and {@link ReactionAdd} sits in the card's action row with edit,
 * delete and the drag grip — which is where a control belongs and where the
 * eye already goes looking for one.
 *
 * Showing only emoji that already have a count is what keeps cards readable —
 * eight zero-count chips per card would be more chrome than content. The picker
 * is a `role="menu"`, so a screen reader announces "menu, 8 items" rather than
 * eight loose buttons appearing from nowhere.
 *
 * The old implementation positioned a single shared `#rx-picker` element by
 * hand: `getBoundingClientRect`, clamp to `innerWidth`, flip above if it
 * overflowed the bottom. That existed only because the picker had to survive
 * the `innerHTML =` re-render that wiped the card every 1.2 s. Nothing wipes a
 * card here, so the picker can live inside it and be positioned by CSS.
 */

import { useEffect, useRef, useState } from 'react';

import { cx } from '../runtime/cx';
import { REACTION_EMOJIS } from '../types/enums';
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

export interface ReactionAddProps {
  mine: ReadonlySet<string>;
  onReact(emoji: string): void;
  disabled?: boolean;
}

export function ReactionAdd({ mine, onReact, disabled }: ReactionAddProps) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLSpanElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const dismiss = (event: Event): void => {
      const wrap = wrapRef.current;
      if (wrap && event.target instanceof Node && !wrap.contains(event.target)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent): void => {
      if (event.key !== 'Escape') return;
      setOpen(false);
      triggerRef.current?.focus();
    };
    document.addEventListener('pointerdown', dismiss, true);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', dismiss, true);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <span className={styles['rxAddWrap']} ref={wrapRef}>
      <button
        ref={triggerRef}
        type="button"
        className={styles['act']}
        disabled={disabled}
        aria-label="Add a reaction"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span aria-hidden="true">☺</span>
      </button>

      {open ? (
        <div className={styles['rxPicker']} role="menu" aria-label="Reactions">
          {REACTION_EMOJIS.map((emoji) => (
            <button
              key={emoji}
              type="button"
              role="menuitem"
              className={cx(styles['rxPick'], mine.has(emoji) && styles['rxChipMine'])}
              aria-label={emoji}
              onClick={() => {
                onReact(emoji);
                setOpen(false);
                triggerRef.current?.focus();
              }}
            >
              <span aria-hidden="true">{emoji}</span>
            </button>
          ))}
        </div>
      ) : null}
    </span>
  );
}
