/**
 * One retro column: a head, a scrolling stack of cards, and its own composer.
 *
 * The composer is a *sibling* of the scrolling stack, not a child of it — see
 * {@link ColumnComposer} for why that matters and why it replaced the single
 * board-wide composer. The `+` that used to live in the head has gone with it:
 * with a visible "Add a card" row pinned in the column, a second affordance for
 * the same action ten centimetres above it is clutter, and two buttons in one
 * column with the same accessible name are worse than clutter.
 */

import { useState } from 'react';

import { toneVar } from '../design/tone';
import { Ticker } from '../motion';
import { TypingIndicator } from '../shared';
import { cx } from '../runtime/cx';
import { RETRO_GRID_LABELS, type RetroGrids } from '../types/enums';
import type { RetroCard } from '../types/board';
import { CardView } from './CardView';
import { ColumnComposer } from './ColumnComposer';
import { GRID_TONE } from './gridTone';
import type { DropTarget } from './useCardDrag';
import motion from '../motion/motion.module.css';
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
  /** Card ids that just arrived from a peer, for the entrance animation. */
  arrivals: ReadonlySet<string>;
  locked: boolean;
  /** Cluster cards under an author heading instead of listing them flat. */
  grouped: boolean;
  /** Only this author's cards are shown, during a walkthrough. */
  focus: string;
  /** Where a card would land if dropped now — `null` when not over this column. */
  dropAt: DropTarget | null;
  draggingId: string | null;
  /** A card written in this column's own composer. */
  onAddCard(text: string): void;
  /** Fired as you type into this column, for the peer "is writing" ghost. */
  onTyping(): void;
  onEdit(cardId: string, text: string): void;
  onDelete(cardId: string): void;
  onReact(cardId: string, emoji: string): void;
  /** A press on the card body, which a mouse turns into a drag at once. */
  onCardPointerDown(cardId: string, event: PointerEvent): void;
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
  arrivals,
  locked,
  grouped,
  focus,
  dropAt,
  draggingId,
  onAddCard,
  onTyping,
  onEdit,
  onDelete,
  onReact,
  onCardPointerDown,
}: ColumnProps) {
  const [composing, setComposing] = useState(false);
  const [focusNonce, setFocusNonce] = useState(0);
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

  const renderCard = (card: RetroCard, last: boolean) => (
    <div
      key={card.id}
      // Both classes, not one: `enter` places the card and `arrived` decays an
      // accent edge over the next 700ms, so a facilitator who was looking at
      // another column can still find what moved.
      className={cx(styles['cardSlot'], arrivals.has(card.id) && motion['enter'])}
    >
      {dropAt && dropAt.index === positions.get(card.id) ? (
        <div className={styles['dropLine']} data-drop-line="lead" aria-hidden="true" />
      ) : null}
      {/* Past the last card. Rendered inside the slot rather than after it: the
          indicator is positioned out of the flow, and a sibling in the flow
          would push every card below it by its own height plus a gap — which
          moves the very midpoints the drop index was computed from. */}
      {last && dropAt && dropAt.index >= slots ? (
        <div className={cx(styles['dropLine'], styles['dropLineTail'])} data-drop-line="tail" aria-hidden="true" />
      ) : null}
      <CardView
        arrived={arrivals.has(card.id)}
        card={card}
        authorAvatar={avatars.get(card.author)}
        myReactions={myReactions.get(card.id) ?? NO_REACTIONS}
        locked={locked}
        dragging={draggingId === card.id}
        onEdit={onEdit}
        onDelete={onDelete}
        onReact={onReact}
        onCardPointerDown={onCardPointerDown}
      />
    </div>
  );

  const tone = GRID_TONE[grid];

  return (
    <section
      className={styles['column']}
      aria-labelledby={`col-${grid}`}
      // The column's identity, as one custom property the stylesheet reads. Set
      // here rather than as four hand-written CSS rules so that the mapping
      // lives in one typed place (gridTone.ts) and a new grid cannot ship
      // uncoloured.
      style={{ '--col-tone': toneVar(tone) } as never}
    >
      <header className={styles['columnHead']}>
        {/* The heading is mono and uppercase like every other label; the count
            rides in it rather than beside it, because a column head's useful
            information is "how much is in here", not a sequence number. */}
        <h2 id={`col-${grid}`} className={styles['columnTitle']}>
          {label}
        </h2>
        <span className={styles['columnDot']} aria-hidden="true">
          ·
        </span>
        <Ticker value={visible.length} className={styles['columnCount']} />
      </header>

      <div className={cx(styles['cards'], dropAt && styles['cardsOver'])} data-grid={grid}>
        {typing.length > 0 ? (
          // A ghost card where the real one is about to land. The server has
          // always tracked typing; it used to be rendered only as a line of
          // text under the column, which is not where you are looking.
          <div className={motion['ghost']} aria-hidden="true">
            {typing.length === 1 ? `${typing[0]} is writing` : `${typing.length} people are writing`}
          </div>
        ) : null}
        {visible.length === 0 && typing.length === 0 ? (
          <p className={styles['columnEmpty']}>{focus ? `Nothing from ${focus} here.` : 'Nothing yet.'}</p>
        ) : grouped ? (
          groupByAuthor(visible).map(([author, group]) => (
            <div key={author} className={styles['authorGroup']}>
              <h3 className={styles['authorGroupHead']}>{author}</h3>
              {group.map((card, index) => renderCard(card, index === group.length - 1))}
            </div>
          ))
        ) : (
          visible.map((card, index) => renderCard(card, index === visible.length - 1))
        )}
        {/* An empty column has no last slot to hang the tail indicator in. */}
        {visible.length === 0 && dropAt ? <div className={styles['dropLine']} data-drop-line="empty" aria-hidden="true" /> : null}

        {/* Last in the stack, so a card lands where you were pointing and the
            rest of the column is the control that opens it. */}
        {locked ? null : (
          <ColumnComposer
            label={label}
            open={composing}
            focusNonce={focusNonce}
            onOpen={() => {
              setComposing(true);
              setFocusNonce((n) => n + 1);
            }}
            onClose={() => setComposing(false)}
            onSubmit={onAddCard}
            onTyping={onTyping}
          />
        )}
      </div>

      {/* The ghost above says this visually and is aria-hidden; this keeps the
          announcement without printing the same sentence twice on screen. */}
      <TypingIndicator names={typing} className={styles['srOnly']} />
    </section>
  );
}
