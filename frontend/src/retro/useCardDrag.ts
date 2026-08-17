/**
 * Dragging a card between columns, with a pointer instead of HTML5 drag-and-drop.
 *
 * ## Why not `draggable="true"`
 *
 * The current board uses the HTML5 drag API. `dragstart` and `drop` **do not
 * fire for touch input** in any mobile browser, so moving a card on a phone is
 * not merely awkward, it is impossible — and a retro board whose whole purpose
 * is a tunnel link people open on their phones cannot have a core interaction
 * that only works with a mouse.
 *
 * ## Why not @dnd-kit
 *
 * The plan named `@dnd-kit/core` + `sortable`. Two reasons this does the ~150
 * lines instead. It is a React library and this bundle is `preact/compat`, so
 * "it works" would be an assumption to verify in a browser rather than a fact —
 * and a drag that fails only over the tunnel is exactly the class of bug the
 * CSP guard exists to prevent. And it is ~13 KB gzipped on a page inlined into
 * every board load over a phone connection, when choosing preact over React to
 * save 170 KB was the whole reason the runtime is what it is. The requirement
 * here is one card into one column at one index, not a general sortable tree.
 *
 * ## Where a drag starts
 *
 * Anywhere on the card with a mouse, and from the grip or a short hold with a
 * finger. The split is not a preference: a pointer drag needs the browser to
 * stop treating the gesture as a scroll, and on touch the only moment that
 * decision can be made is before the first move — so a card that grabbed the
 * whole surface immediately would be a column you can no longer scroll. A hold
 * is the gesture that says "this one", and the grip stays for anyone who would
 * rather not wait for it.
 *
 * Keyboard users do not drag at all: the grip is a real button that opens a
 * "Move to…" menu (see CardView). Emulating a drag with arrow keys is a worse
 * interaction than simply naming the destination.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import type { RetroGrids } from '../types/enums';

/** Pixels of movement before a press becomes a drag, so a tap still taps. */
const THRESHOLD = 4;
/** How long a finger rests on a card before it can be carried, in ms. */
const HOLD_MS = 300;
/** How far a finger may stray during that hold and still arm the drag, in px. */
const HOLD_SLOP = 8;
/** Distance from a column's edge at which it starts auto-scrolling, in px. */
const EDGE = 56;
/** Auto-scroll speed at the very edge, in px per frame. */
const SCROLL_RATE = 12;
/** Degrees of lean at full tilt. */
const TILT_MAX = 9;
/** Horizontal speed, in px per event, that reaches it. */
const TILT_SPEED = 14;
/** Quiet time after which a carried card swings back upright, in ms. */
const SETTLE_MS = 70;

export interface DropTarget {
  grid: RetroGrids;
  /** Insertion index within that column. */
  index: number;
}

export interface DragState {
  cardId: string;
  /** Viewport coordinates of the pointer. */
  x: number;
  y: number;
  /** Where inside the card it was grabbed, so it hangs where it was picked up. */
  grabX: number;
  grabY: number;
  /** The card's own width, so the carried copy is not a different object. */
  width: number;
  /** Lean, in degrees, from how fast it is being swung. */
  tilt: number;
  target: DropTarget | null;
}

export interface CardDragOptions {
  onMove(cardId: string, grid: RetroGrids, index: number): void;
  /** Names the columns, for the announcement. */
  gridLabel(grid: RetroGrids): string;
  enabled?: boolean;
}

export interface CardDrag {
  drag: DragState | null;
  /** Attach to the grip element of the card with this id. */
  onGripPointerDown(cardId: string, event: PointerEvent): void;
  /** Attach to the card body. Mice pick up at once; fingers hold first. */
  onCardPointerDown(cardId: string, event: PointerEvent): void;
  /** Live-region text describing the drag. Render it in an `aria-live` node. */
  announcement: string;
}

/** The column element under a point, if any. */
function columnAt(x: number, y: number): HTMLElement | null {
  const el = document.elementFromPoint(x, y);
  return el instanceof Element ? el.closest<HTMLElement>('[data-grid]') : null;
}

/**
 * Where a card dropped at `y` should land in `column`.
 *
 * Compared against each card's vertical midpoint, and the dragged card itself is
 * skipped — counting it would make "drop where it already is" compute an index
 * one past its own position and register as a move.
 */
function indexAt(column: HTMLElement, y: number, draggedId: string): number {
  const cards = [...column.querySelectorAll<HTMLElement>('[data-card-id]')].filter(
    (el) => el.dataset['cardId'] !== draggedId
  );
  for (let i = 0; i < cards.length; i += 1) {
    const rect = (cards[i] as HTMLElement).getBoundingClientRect();
    if (y < rect.top + rect.height / 2) return i;
  }
  return cards.length;
}

/** Nudge a column that the pointer is hovering near the top or bottom edge of. */
function autoScroll(column: HTMLElement, y: number): void {
  const rect = column.getBoundingClientRect();
  if (column.scrollHeight <= column.clientHeight) return;
  const fromTop = y - rect.top;
  const fromBottom = rect.bottom - y;
  if (fromTop < EDGE) column.scrollTop -= SCROLL_RATE * (1 - fromTop / EDGE);
  else if (fromBottom < EDGE) column.scrollTop += SCROLL_RATE * (1 - fromBottom / EDGE);
}

export function useCardDrag({ onMove, gridLabel, enabled = true }: CardDragOptions): CardDrag {
  const [drag, setDrag] = useState<DragState | null>(null);
  const [announcement, setAnnouncement] = useState('');

  // The live drag lives in a ref as well as in state: the pointermove handler
  // needs the current target synchronously to decide whether anything changed,
  // and reading it from state would give it the value from the render that
  // installed the listener.
  const active = useRef<DragState | null>(null);
  const handlers = useRef({ onMove, gridLabel });
  handlers.current = { onMove, gridLabel };

  // Set for the lifetime of one press. Held here so unmounting mid-drag can run
  // it — the listeners live on `document`, so without this a board torn down
  // with a card in the air keeps four handlers alive against a dead component.
  const teardownRef = useRef<(() => void) | null>(null);

  const finish = useCallback((commit: boolean) => {
    const current = active.current;
    active.current = null;
    setDrag(null);
    if (!current) return;
    if (commit && current.target) {
      handlers.current.onMove(current.cardId, current.target.grid, current.target.index);
      // 1-based: "position 0" is not something anyone says out loud.
      setAnnouncement(
        `Moved to ${handlers.current.gridLabel(current.target.grid)}, position ${current.target.index + 1}.`
      );
    } else if (commit) {
      // Released over the toolbar, the composer, or the gap between columns.
      // Distinct from a cancel: nothing went wrong and nothing was undone, and
      // saying "cancelled" would suggest the move had been rejected.
      setAnnouncement('Dropped outside a column. The card did not move.');
    } else {
      setAnnouncement('Move cancelled.');
    }
  }, []);

  // Cleared on every move and re-armed, so it only fires once the pointer has
  // actually stopped. That gap is the swing back to upright.
  const settleRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  /**
   * One press, from either the grip or the card body.
   *
   * `hold` is the touch path: the drag arms on a timer instead of on movement,
   * and until it does every move is left alone so the column still scrolls.
   */
  const begin = useCallback(
    (cardId: string, event: PointerEvent, hold: boolean) => {
      // Secondary buttons open context menus; hijacking them would make
      // right-click-to-inspect start a drag instead.
      if (!enabled || (event.button !== undefined && event.button !== 0)) return;

      const from = event.currentTarget;
      const card = from instanceof Element ? from.closest<HTMLElement>('[data-card-id]') : null;
      const rect = card?.getBoundingClientRect();
      const grabX = rect ? event.clientX - rect.left : 0;
      const grabY = rect ? event.clientY - rect.top : 0;
      const width = rect?.width ?? 0;

      const originX = event.clientX;
      const originY = event.clientY;
      let started = false;
      let lastX = originX;
      let armTimer: ReturnType<typeof setTimeout> | undefined;

      if (!hold) event.preventDefault();

      const pick = (e: PointerEvent): void => {
        started = true;
        // Capture only once the drag is real. Capturing on pointerdown would
        // swallow the click that a plain tap on the grip should produce — the
        // tap is what opens the keyboard "Move to…" menu.
        try {
          if (from instanceof Element) from.setPointerCapture(e.pointerId);
        } catch {
          // No pointer capture (jsdom, or the pointer already ended). The
          // document-level listeners below carry the drag either way.
        }
        // A drag across a board of text otherwise paints half of it blue.
        document.body.style.userSelect = 'none';
        setAnnouncement('Picked up. Drag to a column, or press Escape to cancel.');
      };

      const at = (e: PointerEvent, tilt: number): DragState => {
        const column = columnAt(e.clientX, e.clientY);
        let target: DropTarget | null = null;
        if (column) {
          autoScroll(column, e.clientY);
          const grid = column.dataset['grid'] as RetroGrids | undefined;
          if (grid) target = { grid, index: indexAt(column, e.clientY, cardId) };
        }
        return { cardId, x: e.clientX, y: e.clientY, grabX, grabY, width, tilt, target };
      };

      const move = (e: PointerEvent): void => {
        if (e.pointerId !== event.pointerId) return;
        if (!started) {
          if (hold) {
            // Strayed before the hold elapsed: this was a scroll.
            if (Math.hypot(e.clientX - originX, e.clientY - originY) > HOLD_SLOP) teardown();
            return;
          }
          if (Math.hypot(e.clientX - originX, e.clientY - originY) < THRESHOLD) return;
          pick(e);
        }
        // Armed: the gesture is ours, and the browser must not also scroll with it.
        e.preventDefault();

        const speed = e.clientX - lastX;
        lastX = e.clientX;
        const tilt = Math.max(-TILT_MAX, Math.min(TILT_MAX, (speed / TILT_SPEED) * TILT_MAX));
        const next = at(e, tilt);
        active.current = next;
        setDrag(next);

        clearTimeout(settleRef.current);
        settleRef.current = setTimeout(() => {
          const held = active.current;
          if (!held) return;
          const upright = { ...held, tilt: 0 };
          active.current = upright;
          setDrag(upright);
        }, SETTLE_MS);
      };

      const up = (e: PointerEvent): void => {
        if (e.pointerId !== event.pointerId) return;
        teardown();
        // A press that never crossed the threshold is a click, not a drag —
        // let it through untouched so the menu opens.
        if (started) finish(true);
      };

      const cancel = (e: PointerEvent): void => {
        if (e.pointerId !== event.pointerId) return;
        teardown();
        if (started) finish(false);
      };

      const key = (e: KeyboardEvent): void => {
        if (e.key !== 'Escape') return;
        teardown();
        finish(false);
      };

      function teardown(): void {
        document.body.style.userSelect = '';
        clearTimeout(armTimer);
        clearTimeout(settleRef.current);
        document.removeEventListener('pointermove', move);
        document.removeEventListener('pointerup', up);
        document.removeEventListener('pointercancel', cancel);
        document.removeEventListener('keydown', key);
        teardownRef.current = null;
      }

      if (hold) {
        armTimer = setTimeout(() => {
          if (started) return;
          pick(event);
          const next: DragState = {
            cardId,
            x: originX,
            y: originY,
            grabX,
            grabY,
            width,
            tilt: 0,
            target: null,
          };
          active.current = next;
          setDrag(next);
        }, HOLD_MS);
      }

      // `passive: false` on the move listener, or `preventDefault` above is
      // ignored on touch and the column scrolls out from under the card.
      document.addEventListener('pointermove', move, { passive: false });
      document.addEventListener('pointerup', up);
      document.addEventListener('pointercancel', cancel);
      document.addEventListener('keydown', key);
      teardownRef.current = teardown;
    },
    [enabled, finish]
  );

  const onGripPointerDown = useCallback(
    (cardId: string, event: PointerEvent) => begin(cardId, event, false),
    [begin]
  );

  const onCardPointerDown = useCallback(
    (cardId: string, event: PointerEvent) => {
      // A press on a control inside the card is that control's, not a grab.
      const from = event.target;
      if (from instanceof Element && from.closest('button, a, input, textarea, select')) return;
      begin(cardId, event, event.pointerType !== 'mouse');
    },
    [begin]
  );

  useEffect(
    () => () => {
      teardownRef.current?.();
      clearTimeout(settleRef.current);
      active.current = null;
    },
    []
  );

  return { drag, onGripPointerDown, onCardPointerDown, announcement };
}
