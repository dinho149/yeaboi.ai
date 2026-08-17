/**
 * The four columns, plus everything that spans them: drag, and the phone pager.
 *
 * ## Layout
 *
 * Three regimes, on the shared breakpoints in tokens.css. Wide (≥1100 px) is
 * four equal columns. Medium is a 2×2 grid — four columns squeezed into a
 * laptop half-screen gave every card a ~180 px measure, which is narrower than
 * a sentence. Narrow (<700 px) is one column per screen with horizontal
 * scroll-snap and a dot pager, because four columns on a phone means four
 * unreadable slivers, and a vertical stack means scrolling past twelve cards to
 * reach the next heading.
 *
 * The pager is deliberately CSS scroll-snap driven, not JS-driven: swiping is
 * the native gesture and the dots follow it. Tapping a dot scrolls; the scroll
 * position remains the single source of truth either way.
 */

import { useCallback, useMemo, useRef, useState } from 'react';

import { cx } from '../runtime/cx';
import { RETRO_GRID_LABELS, RETRO_GRIDS, type RetroGrids } from '../types/enums';
import type { RetroCard } from '../types/board';
import { Column } from './Column';
import { DragPreview } from './DragPreview';
import { useCardDrag } from './useCardDrag';
import { useCardFlip } from './useCardFlip';
import { useFrozen } from './useFrozen';
import styles from './retro.module.css';

export interface BoardProps {
  cards: readonly RetroCard[];
  avatars: ReadonlyMap<string, string>;
  myReactions: ReadonlyMap<string, ReadonlySet<string>>;
  /** grid → names typing into it, already excluding yourself. */
  typing: ReadonlyMap<string, readonly string[]>;
  locked: boolean;
  focus: string;
  /** Card ids that just arrived from a peer, for the entrance animation. */
  arrivals: ReadonlySet<string>;
  onAddCard(grid: RetroGrids, text: string): void;
  onTyping(grid: RetroGrids): void;
  onEdit(cardId: string, text: string): void;
  onDelete(cardId: string): void;
  onReact(cardId: string, emoji: string): void;
  onMove(cardId: string, grid: RetroGrids, index: number): void;
  /** Set while a sprint switch is swapping one board for another. */
  className?: string | undefined;
}

const NO_TYPING: readonly string[] = [];

export function Board({
  cards,
  avatars,
  myReactions,
  typing,
  locked,
  focus,
  arrivals,
  onAddCard,
  onTyping,
  onEdit,
  onDelete,
  onReact,
  onMove,
  className,
}: BoardProps) {
  const trackRef = useRef<HTMLDivElement | null>(null);
  const [page, setPage] = useState(0);
  // One draft at a time. Owned here rather than per column, so opening a
  // composer closes whichever one was already open — four half-written cards
  // across the board is four things you have to remember to finish.
  const [composing, setComposing] = useState<RetroGrids | ''>('');
  const [focusNonce, setFocusNonce] = useState(0);

  const gridLabel = useCallback((grid: RetroGrids) => RETRO_GRID_LABELS[grid], []);
  // What the flip watches: a card in a different column, or in a different
  // place in one. Anything else that moves a card is not a move.
  const arrangement = useMemo(() => cards.map((card) => `${card.id}:${card.grid}`).join(), [cards]);
  // The move tells the flip which card not to replay: that one has its own
  // flight from the pointer. Through a ref, because the flip needs the drag's
  // state and the drag needs this callback — the cycle has to break somewhere.
  const skipRef = useRef<((cardId: string) => void) | null>(null);
  const onMoveCard = useCallback(
    (cardId: string, grid: RetroGrids, index: number) => {
      skipRef.current?.(cardId);
      onMove(cardId, grid, index);
    },
    [onMove]
  );
  const { drag, previewRef, onCardPointerDown, announcement } = useCardDrag({
    onMove: onMoveCard,
    gridLabel,
    enabled: !locked,
  });
  // `drag !== null`, not a ref holding it: the render that matters is the one
  // where the drag ends and the list unfreezes, and a ref written after this
  // call still holds the previous render's value on exactly that render.
  const flip = useCardFlip(trackRef, drag !== null, arrangement);
  skipRef.current = flip.skipOnce;

  // Hold the card list still for the duration of a drag. See useFrozen.
  const stable = useFrozen(cards, drag !== null);

  const byGrid = useMemo(() => {
    const map = new Map<RetroGrids, RetroCard[]>(RETRO_GRIDS.map((grid) => [grid, []]));
    for (const card of stable) map.get(card.grid)?.push(card);
    return map;
  }, [stable]);

  const dragged = drag ? (stable.find((card) => card.id === drag.cardId) ?? null) : null;

  const onTrackScroll = (): void => {
    const track = trackRef.current;
    if (!track || track.clientWidth === 0) return;
    setPage(Math.round(track.scrollLeft / track.clientWidth));
  };

  return (
    <>
      <div
        className={cx(styles['board'], className)}
        ref={trackRef}
        onScroll={onTrackScroll}
        // Suppresses the resting cards' hover lift: the carried card takes no
        // pointer events, so without this every card it passes over animates.
        data-dragging={drag ? 'true' : undefined}
      >
        {RETRO_GRIDS.map((grid) => (
          <Column
            key={grid}
            grid={grid}
            cards={byGrid.get(grid) ?? []}
            avatars={avatars}
            myReactions={myReactions}
            typing={typing.get(grid) ?? NO_TYPING}
            arrivals={arrivals}
            locked={locked}
            focus={focus}
            dropAt={drag?.target?.grid === grid ? drag.target : null}
            draggingId={drag?.cardId ?? null}
            composing={composing === grid}
            focusNonce={focusNonce}
            onOpenComposer={() => {
              setComposing(grid);
              setFocusNonce((n) => n + 1);
            }}
            onCloseComposer={() => setComposing('')}
            onAddCard={(text) => onAddCard(grid, text)}
            onTyping={() => onTyping(grid)}
            onEdit={onEdit}
            onDelete={onDelete}
            onReact={onReact}
            onCardPointerDown={onCardPointerDown}
          />
        ))}
      </div>

      {/* Phone pager. Hidden above the narrow breakpoint by CSS rather than by a
          media query in JS, so there is no resize listener and no layout read. */}
      <div className={styles['pager']} role="tablist" aria-label="Columns">
        {RETRO_GRIDS.map((grid, index) => (
          <button
            key={grid}
            type="button"
            role="tab"
            aria-selected={index === page}
            aria-label={RETRO_GRID_LABELS[grid]}
            className={cx(styles['pagerDot'], index === page && styles['pagerDotOn'])}
            onClick={() => {
              const track = trackRef.current;
              track?.scrollTo({ left: index * track.clientWidth, behavior: 'smooth' });
            }}
          />
        ))}
      </div>

      {dragged ? (
        <DragPreview
          previewRef={previewRef}
          drag={drag as NonNullable<typeof drag>}
          card={dragged}
          authorAvatar={avatars.get(dragged.author)}
        />
      ) : null}

      {/* The drag's running commentary. A pointer drag is invisible to a screen
          reader otherwise, and this is also what confirms a move landed. */}
      <div className={styles['srOnly']} role="status" aria-live="polite" aria-atomic="true">
        {announcement}
      </div>
    </>
  );
}
