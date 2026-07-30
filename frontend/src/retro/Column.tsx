/**
 * One retro column.
 *
 * The `+` in the heading does not open a form here — it focuses the single
 * composer at the bottom of the screen with this column preselected. That is
 * the visible consequence of collapsing four textareas into one: the affordance
 * stays where you expect it, the writing surface stops being duplicated four
 * times, and the primary action lives in the thumb zone on a phone instead of
 * at the bottom of whichever column you happened to scroll.
 */

import { TypingIndicator } from '../shared';
import { cx } from '../runtime/cx';
import { RETRO_GRID_LABELS, type RetroGrids } from '../types/enums';
import type { RetroCard } from '../types/board';
import { CardView } from './CardView';
import type { DropTarget } from './useCardDrag';
import styles from './retro.module.css';

const NO_REACTIONS: ReadonlySet<string> = new Set();

export interface ColumnProps {
  grid: RetroGrids;
  cards: readonly RetroCard[];
  /** Name → avatar, from the presence roster. */
  avatars: ReadonlyMap<string, string>;
  /** card id → the emoji this browser reacted with. */
  myReactions: ReadonlyMap<string, ReadonlySet<string>>;
  /** Names typing into this column, excluding yourself. */
  typing: readonly string[];
  locked: boolean;
  /** Cluster cards under an author heading instead of listing them flat. */
  grouped: boolean;
  /** Only this author's cards are shown, during a walkthrough. */
  focus: string;
  /** Where a card would land if dropped now — `null` when not over this column. */
  dropAt: DropTarget | null;
  draggingId: string | null;
  onCompose(): void;
  onEdit(cardId: string, text: string): void;
  onDelete(cardId: string): void;
  onReact(cardId: string, emoji: string): void;
  onMoveTo(cardId: string, grid: RetroGrids): void;
  onGripPointerDown(cardId: string, event: PointerEvent): void;
}

/** Cards clustered by author, first-seen order preserved. */
function groupByAuthor(cards: readonly RetroCard[]): [string, RetroCard[]][] {
  const groups = new Map<string, RetroCard[]>();
  for (const card of cards) {
    const key = card.origin === 'ai' ? '🤖 AI' : card.author;
    const bucket = groups.get(key);
    if (bucket) bucket.push(card);
    else groups.set(key, [card]);
  }
  return [...groups.entries()];
}

export function Column({
  grid,
  cards,
  avatars,
  myReactions,
  typing,
  locked,
  grouped,
  focus,
  dropAt,
  draggingId,
  onCompose,
  onEdit,
  onDelete,
  onReact,
  onMoveTo,
  onGripPointerDown,
}: ColumnProps) {
  const label = RETRO_GRID_LABELS[grid];
  const visible = focus ? cards.filter((card) => card.author === focus) : cards;

  // Drop positions skip the card being dragged, matching `indexAt` in
  // useCardDrag — count it and the indicator sits one slot off whenever you
  // drag a card within its own column.
  const positions = new Map<string, number>();
  for (const card of visible) {
    if (card.id !== draggingId) positions.set(card.id, positions.size);
  }
  const slots = positions.size;

  const renderCard = (card: RetroCard) => (
    <div key={card.id} className={styles['cardSlot']}>
      {dropAt && dropAt.index === positions.get(card.id) ? (
        <div className={styles['dropLine']} aria-hidden="true" />
      ) : null}
      <CardView
        card={card}
        authorAvatar={avatars.get(card.author)}
        myReactions={myReactions.get(card.id) ?? NO_REACTIONS}
        locked={locked}
        dragging={draggingId === card.id}
        onEdit={(text) => onEdit(card.id, text)}
        onDelete={() => onDelete(card.id)}
        onReact={(emoji) => onReact(card.id, emoji)}
        onMoveTo={(target) => onMoveTo(card.id, target)}
        onGripPointerDown={(event) => onGripPointerDown(card.id, event)}
      />
    </div>
  );

  return (
    <section className={styles['column']} aria-labelledby={`col-${grid}`}>
      <header className={styles['columnHead']}>
        <h2 id={`col-${grid}`} className={styles['columnTitle']}>
          {label}
        </h2>
        <span className={styles['columnCount']}>{visible.length}</span>
        {locked ? null : (
          <button
            type="button"
            className={styles['columnAdd']}
            aria-label={`Add a card to ${label}`}
            onClick={onCompose}
          >
            <span aria-hidden="true">+</span>
          </button>
        )}
      </header>

      <div className={cx(styles['cards'], dropAt && styles['cardsOver'])} data-grid={grid}>
        {visible.length === 0 ? (
          <p className={styles['columnEmpty']}>{focus ? `Nothing from ${focus} here.` : 'Nothing yet.'}</p>
        ) : grouped ? (
          groupByAuthor(visible).map(([author, group]) => (
            <div key={author} className={styles['authorGroup']}>
              <h3 className={styles['authorGroupHead']}>{author}</h3>
              {group.map(renderCard)}
            </div>
          ))
        ) : (
          visible.map(renderCard)
        )}
        {/* Trailing indicator, for a drop past the last card. */}
        {dropAt && dropAt.index >= slots ? <div className={styles['dropLine']} aria-hidden="true" /> : null}
      </div>

      <TypingIndicator names={typing} className={styles['columnTyping']} />
    </section>
  );
}
