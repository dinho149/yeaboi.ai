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

import { useCallback, useEffect, useRef, useState, type MutableRefObject } from 'react';

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
/** Horizontal speed, in px per *frame*, that reaches it. */
const TILT_SPEED = 40;
/** Quiet time after which a carried card swings back upright, in ms. */
const SETTLE_MS = 70;
/** How long the released card takes to fly into the slot it was dropped on. */
const LAND_MS = 220;

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
  /**
   * Attach to the carried card. Its transform is written here rather than
   * rendered, so following the pointer costs no React work.
   */
  previewRef: MutableRefObject<HTMLElement | null>;
  /** Attach to the grip element of the card with this id. */
  onGripPointerDown(cardId: string, event: PointerEvent): void;
  /** Attach to the card body. Mice pick up at once; fingers hold first. */
  onCardPointerDown(cardId: string, event: PointerEvent): void;
  /** Live-region text describing the drag. Render it in an `aria-live` node. */
  announcement: string;
}

/**
 * Where the carried card hangs.
 *
 * The lean is the separate `rotate` property, not part of this transform, so
 * the stylesheet can transition it without also smoothing — and so visibly
 * lagging — the position. Individual transform properties compose after
 * `transform`, which puts the translation first and the pivot where the card
 * is being held.
 */
export function carriedTransform(drag: DragState): string {
  return `translate3d(${drag.x - drag.grabX}px, ${drag.y - drag.grabY}px, 0)`;
}

/**
 * Everything the drag needs to know about the board, measured once.
 *
 * The hit test used to run `elementFromPoint` and then `getBoundingClientRect`
 * on every card in the column — on the frame that had just written the carried
 * card's transform, so each one forced a synchronous layout of the whole
 * document. The card list is frozen for the duration of a drag (see useFrozen),
 * so nothing can move except by the column scrolling, which is one number.
 */
interface Zone {
  grid: RetroGrids;
  el: HTMLElement;
  left: number;
  right: number;
  top: number;
  bottom: number;
  /** Whether the column can scroll at all, so the edge nudge can be skipped. */
  scrollable: boolean;
  /** Its scroll offset when measured; the nudge below moves it from here. */
  scroll0: number;
  /** Each resting card's vertical midpoint, in viewport coordinates. */
  mids: number[];
}

function measure(draggedId: string): Zone[] {
  return [...document.querySelectorAll<HTMLElement>('[data-grid]')].map((el) => {
    const rect = el.getBoundingClientRect();
    const mids = [...el.querySelectorAll<HTMLElement>('[data-card-id]')]
      .filter((card) => card.dataset['cardId'] !== draggedId)
      .map((card) => {
        const box = card.getBoundingClientRect();
        return box.top + box.height / 2;
      });
    return {
      grid: el.dataset['grid'] as RetroGrids,
      el,
      left: rect.left,
      right: rect.right,
      top: rect.top,
      bottom: rect.bottom,
      scrollable: el.scrollHeight > el.clientHeight,
      scroll0: el.scrollTop,
      mids,
    };
  });
}

/** Nudge a column the pointer is hovering near the top or bottom edge of. */
function autoScroll(zone: Zone, y: number): void {
  if (!zone.scrollable) return;
  const fromTop = y - zone.top;
  const fromBottom = zone.bottom - y;
  if (fromTop < EDGE) zone.el.scrollTop -= SCROLL_RATE * (1 - fromTop / EDGE);
  else if (fromBottom < EDGE) zone.el.scrollTop += SCROLL_RATE * (1 - fromBottom / EDGE);
}

/** Where a card released at `y` would land in `zone`. */
function indexIn(zone: Zone, y: number): number {
  const shift = zone.el.scrollTop - zone.scroll0;
  let index = 0;
  while (index < zone.mids.length && y > (zone.mids[index] as number) - shift) index += 1;
  return index;
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

  const previewRef = useRef<HTMLElement | null>(null);

  /**
   * Fly the released card into the slot it was dropped on.
   *
   * The indicator is already drawn exactly where the card is going, so it is
   * the target — no second guess at the landing position, and it is right
   * whichever column and index the drop resolved to. Resolves when the card has
   * arrived, so the caller knows when to stop rendering it.
   */
  const land = useCallback((): Promise<void> => {
    const el = previewRef.current;
    const line = document.querySelector<HTMLElement>('[data-drop-line]');
    const slot = line?.parentElement;
    if (!el || !line || !slot || typeof el.animate !== 'function') return Promise.resolve();

    // The indicator sits half a gap outside the slot it marks, so landing on it
    // would put the card a few pixels off — and it would snap the moment the
    // real one took its place. The slot is what to aim at.
    const kind = line.dataset['dropLine'];
    const box = slot.getBoundingClientRect();
    const style = getComputedStyle(slot);
    const target =
      kind === 'tail'
        ? { left: box.left, top: box.bottom + (parseFloat(style.rowGap) || 0) }
        : kind === 'empty'
          ? { left: box.left + parseFloat(style.paddingLeft), top: box.top + parseFloat(style.paddingTop) }
          : { left: box.left, top: box.top };

    // The layer is the viewport, and the card is pinned to its top-left corner,
    // so the translation *is* the position — no delta against a measured rect,
    // which is what got this wrong: `getBoundingClientRect` reports the
    // axis-aligned box of a card that is leaning nine degrees and scaled up,
    // and its top is 140px below the card's own.
    const animation = el.animate(
      [
        { transform: el.style.transform, rotate: el.style.rotate || '0deg', scale: '1.03' },
        { transform: `translate3d(${target.left}px, ${target.top}px, 0)`, rotate: '0deg', scale: '1' },
      ],
      { duration: LAND_MS, easing: 'cubic-bezier(0.22, 1, 0.36, 1)', fill: 'forwards' }
    );
    return animation.finished.then(
      () => undefined,
      () => undefined
    );
  }, []);

  const finish = useCallback(
    (commit: boolean) => {
      const current = active.current;
      active.current = null;
      if (!current) {
        setDrag(null);
        return;
      }
      if (commit && current.target) {
        handlers.current.onMove(current.cardId, current.target.grid, current.target.index);
        // 1-based: "position 0" is not something anyone says out loud.
        setAnnouncement(
          `Moved to ${handlers.current.gridLabel(current.target.grid)}, position ${current.target.index + 1}.`
        );
        // Held one flight longer: the list is frozen while a drag is live, so
        // this is also what keeps the slot open underneath the card until it
        // has arrived in it.
        void land().then(() => setDrag(null));
        return;
      }
      if (commit) {
        // Released over the toolbar, the composer, or the gap between columns.
        // Distinct from a cancel: nothing went wrong and nothing was undone, and
        // saying "cancelled" would suggest the move had been rejected.
        setAnnouncement('Dropped outside a column. The card did not move.');
      } else {
        setAnnouncement('Move cancelled.');
      }
      setDrag(null);
    },
    [land]
  );

  // Cleared on every move and re-armed, so it only fires once the pointer has
  // actually stopped. That gap is the swing back to upright.
  const settleRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  /** Move the carried card, without going through React. */
  const place = useCallback((x: number, y: number, tilt: number) => {
    const el = previewRef.current;
    const held = active.current;
    if (!el || !held) return;
    el.style.transform = carriedTransform({ ...held, x, y, tilt });
    el.style.rotate = `${tilt.toFixed(2)}deg`;
  }, []);

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
        zones = measure(cardId);
        setAnnouncement('Picked up. Drag to a column, or press Escape to cancel.');
      };

      let pendingX = originX;
      let pendingY = originY;
      let queued = false;
      let frame = 0;
      let zones: Zone[] = [];

      /**
       * One update, on the frame that will paint it.
       *
       * Pointer events arrive faster than the screen refreshes, and a mouse
       * reporting at 1000 Hz would otherwise run this — a hit test, a
       * measurement of every card in the column, and a React render of the
       * whole board — sixteen times per painted frame.
       */
      const flush = (): void => {
        const x = pendingX;
        const y = pendingY;

        const speed = x - lastX;
        lastX = x;
        const tilt = Math.max(-TILT_MAX, Math.min(TILT_MAX, (speed / TILT_SPEED) * TILT_MAX));
        place(x, y, tilt);

        const zone = zones.find((z) => x >= z.left && x <= z.right && y >= z.top && y <= z.bottom);
        let target: DropTarget | null = null;
        if (zone) {
          autoScroll(zone, y);
          target = { grid: zone.grid, index: indexIn(zone, y) };
        }

        const held = active.current;
        const next: DragState = { cardId, x, y, grabX, grabY, width, tilt, target };
        active.current = next;
        // React only hears about the drop target. The carried card's position
        // is written straight to its own style above, because re-rendering four
        // columns of cards to move one of them is what made this stutter.
        if (!held || held.target?.grid !== target?.grid || held.target?.index !== target?.index) {
          setDrag(next);
        }

        clearTimeout(settleRef.current);
        settleRef.current = setTimeout(() => {
          if (!active.current) return;
          active.current = { ...active.current, tilt: 0 };
          place(active.current.x, active.current.y, 0);
        }, SETTLE_MS);
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
        pendingX = e.clientX;
        pendingY = e.clientY;
        if (queued) return;
        queued = true;
        frame = requestAnimationFrame(() => {
          queued = false;
          flush();
        });
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
        if (frame) cancelAnimationFrame(frame);
        frame = 0;
        queued = false;
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
    [enabled, finish, place]
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

  return { drag, previewRef, onGripPointerDown, onCardPointerDown, announcement };
}
