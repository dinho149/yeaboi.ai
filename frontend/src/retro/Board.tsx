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
import { useCardDrag } from './useCardDrag';
import { useFrozen } from './useFrozen';
import styles from './retro.module.css';

export interface BoardProps {
  cards: readonly RetroCard[];
  avatars: ReadonlyMap<string, string>;
  myReactions: ReadonlyMap<string, ReadonlySet<string>>;
  /** grid → names typing into it, already excluding yourself. */
  typing: ReadonlyMap<string, readonly string[]>;
  locked: boolean;
  grouped: boolean;
  focus: string;
  /** Card ids that just arrived from a peer, for the entrance animation. */
  arrivals: ReadonlySet<string>;
  onCompose(grid: RetroGrids): void;
  onEdit(cardId: string, text: string): void;
  onDelete(cardId: string): void;
  onReact(cardId: string, emoji: string): void;
  onMove(cardId: string, grid: RetroGrids, index: number): void;
}

const NO_TYPING: readonly string[] = [];

export function Board({
  cards,
  avatars,
  myReactions,
  typing,
  locked,
  grouped,
  focus,
  arrivals,
  onCompose,
  onEdit,
  onDelete,
  onReact,
  onMove,
}: BoardProps) {
  const trackRef = useRef<HTMLDivElement | null>(null);
  const [page, setPage] = useState(0);

  const gridLabel = useCallback((grid: RetroGrids) => RETRO_GRID_LABELS[grid], []);
  const { drag, onGripPointerDown, announcement } = useCardDrag({ onMove, gridLabel, enabled: !locked });

  // Hold the card list still for the duration of a drag. See useFrozen.
  const stable = useFrozen(cards, drag !== null);

  const byGrid = useMemo(() => {
    const map = new Map<RetroGrids, RetroCard[]>(RETRO_GRIDS.map((grid) => [grid, []]));
    for (const card of stable) map.get(card.grid)?.push(card);
    return map;
  }, [stable]);

  /**
   * Column widths, proportional to how full each column is.
   *
   * Four equal columns meant a Demos column holding one card took a quarter of
   * a 1440px screen while "What went well" — which in a real retro holds most
   * of the content — was squeezed into the same width. The ratio is clamped to
   * 2:1 and the floor is a minmax in the stylesheet, so a busy column can
   * breathe without a quiet one collapsing to an unreadable strip.
   *
   * Deliberately keyed off the count only, so it changes when a card is added
   * or moved and at no other time. Deriving it from measured content height
   * would reflow the board on every poll.
   */
  const weights = useMemo(
    () =>
      RETRO_GRIDS.map((grid) => {
        const count = byGrid.get(grid)?.length ?? 0;
        const share = Math.min(2, 1 + count / 6);
        return `minmax(0, ${share.toFixed(2)}fr)`;
      }).join(' '),
    [byGrid]
  );

  const onTrackScroll = (): void => {
    const track = trackRef.current;
    if (!track || track.clientWidth === 0) return;
    setPage(Math.round(track.scrollLeft / track.clientWidth));
  };

  return (
    <>
      <div
        className={styles['board']}
        ref={trackRef}
        onScroll={onTrackScroll}
        style={{ '--col-weights': weights } as never}
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
            grouped={grouped}
            focus={focus}
            dropAt={drag?.target?.grid === grid ? drag.target : null}
            draggingId={drag?.cardId ?? null}
            onCompose={() => onCompose(grid)}
            onEdit={onEdit}
            onDelete={onDelete}
            onReact={onReact}
            onMoveTo={(cardId, target) => onMove(cardId, target, (byGrid.get(target) ?? []).length)}
            onGripPointerDown={onGripPointerDown}
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

      {/* The drag's running commentary. A pointer drag is invisible to a screen
          reader otherwise, and this is also what confirms a move landed. */}
      <div className={styles['srOnly']} role="status" aria-live="polite" aria-atomic="true">
        {announcement}
      </div>
    </>
  );
}
