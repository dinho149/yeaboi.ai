/**
 * Cards slide to their new places instead of appearing in them.
 *
 * FLIP: every card is measured after each commit, compared against where it was
 * before it, and — if it moved — animated from the old position to the new one.
 * The layout is never faked; the browser has already put every card where it
 * belongs by the time this runs, and the animation is played backwards from
 * there. So a card pulled out of the middle of a column has the ones below it
 * rise into the gap, and the same happens in the column it landed in.
 *
 * `useLayoutEffect`, because the measurement has to happen before the browser
 * paints the new layout — after it, the jump has already been shown.
 *
 * One card is deliberately not animated: the one just dropped. It has its own
 * flight, from the pointer to the slot (see `land` in useCardDrag), and a FLIP
 * from its old column on top of that would fly it twice.
 */

import { useLayoutEffect, useRef, type RefObject } from 'react';

/** How long a displaced card takes to close the gap, in ms. */
const SLIDE_MS = 260;
const EASE = 'cubic-bezier(0.22, 1, 0.36, 1)';

export interface CardFlip {
  /** Names the card whose next move is its own, and is not to be replayed. */
  skipOnce(cardId: string): void;
}

export function useCardFlip(container: RefObject<HTMLElement | null>): CardFlip {
  const seen = useRef(new Map<string, { left: number; top: number }>());
  const skip = useRef<string | null>(null);

  useLayoutEffect(() => {
    const root = container.current;
    if (!root) return;
    const next = new Map<string, { left: number; top: number }>();

    for (const el of root.querySelectorAll<HTMLElement>('[data-card-id]')) {
      const id = el.dataset['cardId'];
      if (!id) continue;
      const box = el.getBoundingClientRect();
      const now = { left: box.left, top: box.top };
      next.set(id, now);

      const was = seen.current.get(id);
      if (!was || id === skip.current) continue;
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

    skip.current = null;
    seen.current = next;
  });

  return {
    skipOnce: (cardId: string) => {
      skip.current = cardId;
    },
  };
}
