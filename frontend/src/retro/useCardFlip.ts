/**
 * Cards displaced by a move slide into the gap instead of jumping into it.
 *
 * FLIP: every card is measured after each commit and compared with where it was
 * before it. One that moved is animated from the old position to the new. The
 * layout is never faked — the browser has already put every card where it
 * belongs by the time this runs, and the animation is played backwards from
 * there.
 *
 * `useLayoutEffect`, because the measurement has to happen before the browser
 * paints the new layout; after it, the jump has already been shown.
 *
 * Two cards are deliberately never animated. One with no previous measurement,
 * which is every card on a board that has just loaded. And the one just
 * dropped: it has its own flight from the pointer to the slot (see `land` in
 * useCardDrag), and a FLIP from its old column on top of that would fly it
 * twice.
 */

import { useLayoutEffect, useRef, type RefObject } from 'react';

/** How long a displaced card takes to close the gap, in ms. */
const SLIDE_MS = 220;
const EASE = 'cubic-bezier(0.22, 1, 0.36, 1)';

export interface CardFlip {
  /** Names the card whose next move is its own, and is not to be replayed. */
  skipOnce(cardId: string): void;
}

export function useCardFlip(
  container: RefObject<HTMLElement | null>,
  frozen: boolean,
  /**
   * Which card is in which column, as one string.
   *
   * Only a change here is a move. Cards also shift for reasons that are not
   * one — a scrollbar appearing, a composer opening, the board settling on the
   * first poll — and sliding them for those makes the board twitch at rest.
   */
  arrangement: string
): CardFlip {
  const seen = useRef(new Map<string, { left: number; top: number }>());
  const layout = useRef<string | null>(null);
  const skip = useRef<string | null>(null);

  useLayoutEffect(() => {
    const root = container.current;
    if (!root) return;
    const moved = layout.current !== null && layout.current !== arrangement;
    const next = new Map<string, { left: number; top: number }>();

    for (const el of root.querySelectorAll<HTMLElement>('[data-card-id]')) {
      const id = el.dataset['cardId'];
      if (!id) continue;
      const box = el.getBoundingClientRect();
      const now = { left: box.left, top: box.top };
      next.set(id, now);

      const was = seen.current.get(id);
      // Nothing moves while a drag holds the list still, and re-measuring
      // through one would record positions to animate away from afterwards.
      if (frozen || !moved || !was || id === skip.current) continue;
      const dx = was.left - now.left;
      const dy = was.top - now.top;
      // Sub-pixel differences are scroll rounding, not a move.
      if (Math.abs(dx) < 1 && Math.abs(dy) < 1) continue;
      if (typeof el.animate !== 'function') continue;
      el.animate([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: 'none' }], {
        duration: SLIDE_MS,
        easing: EASE,
      });
    }

    // Only spent on a render that could have used it. A drop names the card
    // several frozen renders before the list actually moves, and clearing it on
    // the first of those hands the moved card a slide from its old column on
    // top of the flight it already has.
    if (!frozen && moved) skip.current = null;
    if (!frozen) {
      seen.current = next;
      layout.current = arrangement;
    }
  });

  return {
    skipOnce: (cardId: string) => {
      skip.current = cardId;
    },
  };
}
