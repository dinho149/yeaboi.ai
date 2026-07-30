/**
 * Pointer drag.
 *
 * jsdom has no layout, so the two things a drag reads from the document —
 * `elementFromPoint` and each card's rectangle — are stubbed with a deliberate
 * fake geometry. That is not a weakened test: the arithmetic that decides
 * *which column* and *which index* is exactly what is under test here, and it
 * is the part a browser would not tell you was wrong.
 */

import { act, render, screen } from '@testing-library/preact';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { RetroGrids } from '../types/enums';
import { useCardDrag } from './useCardDrag';

/**
 * Two columns side by side, three cards in the left one.
 *
 *   x <  300 → went_well, cards at y = 0-50, 50-100, 100-150
 *   x >= 300 → demos, empty
 */
const LAYOUT = {
  went_well: { left: 0, right: 300 },
  demos: { left: 300, right: 600 },
} as const;

function Harness({ onMove }: { onMove: (id: string, grid: RetroGrids, index: number) => void }) {
  const { drag, onGripPointerDown, announcement } = useCardDrag({
    onMove,
    gridLabel: (grid) => grid,
  });

  return (
    <div>
      <button type="button" onPointerDown={(e) => onGripPointerDown('c1', e as unknown as PointerEvent)}>
        grip
      </button>
      <div data-grid="went_well" data-testid="col-went_well">
        <div data-card-id="c1" />
        <div data-card-id="c2" />
        <div data-card-id="c3" />
      </div>
      <div data-grid="demos" data-testid="col-demos" />
      <output data-testid="target">{drag?.target ? `${drag.target.grid}:${drag.target.index}` : 'none'}</output>
      <output data-testid="say">{announcement}</output>
    </div>
  );
}

/** Give the fake DOM a geometry, and make `elementFromPoint` agree with it. */
function installLayout(): void {
  for (const [grid, box] of Object.entries(LAYOUT)) {
    const column = screen.getByTestId(`col-${grid}`);
    column.getBoundingClientRect = () =>
      ({ left: box.left, right: box.right, top: 0, bottom: 400, width: box.right - box.left, height: 400 }) as DOMRect;
  }
  const cards = [...document.querySelectorAll<HTMLElement>('[data-card-id]')];
  cards.forEach((card, index) => {
    card.getBoundingClientRect = () =>
      ({ left: 0, right: 300, top: index * 50, bottom: index * 50 + 50, width: 300, height: 50 }) as DOMRect;
  });

  document.elementFromPoint = ((x: number) =>
    screen.getByTestId(x < 300 ? 'col-went_well' : 'col-demos')) as typeof document.elementFromPoint;
}

/** A pointer event jsdom will dispatch. MouseEvent carries the coordinates. */
function pointer(type: string, x: number, y: number): MouseEvent {
  return new MouseEvent(type, { bubbles: true, clientX: x, clientY: y, button: 0 });
}

/**
 * Dispatch and let the resulting render land.
 *
 * The hook's document listeners are outside preact's event system, so the state
 * they set is not flushed by the time `dispatchEvent` returns. `act` is what
 * makes the assertion read the DOM after the render rather than before it.
 */
function fire(target: EventTarget, event: Event): void {
  act(() => {
    target.dispatchEvent(event);
  });
}

afterEach(() => vi.restoreAllMocks());

describe('useCardDrag', () => {
  function setup() {
    const onMove = vi.fn();
    render(<Harness onMove={onMove} />);
    installLayout();
    return { onMove, grip: screen.getByRole('button', { name: 'grip' }) };
  }

  it('moves a card to the column under the pointer', () => {
    const { onMove, grip } = setup();

    fire(grip, pointer('pointerdown', 10, 10));
    fire(document, pointer('pointermove', 400, 20));
    expect(screen.getByTestId('target').textContent).toBe('demos:0');

    fire(document, pointer('pointerup', 400, 20));
    expect(onMove).toHaveBeenCalledWith('c1', 'demos', 0);
  });

  it('inserts above a card whose midpoint the pointer is above', () => {
    const { onMove, grip } = setup();
    fire(grip, pointer('pointerdown', 10, 10));
    // c1 is the card being dragged, so the remaining slots are c2 then c3.
    // y = 60 is inside c2's box (50-100) but above its midpoint (75).
    fire(document, pointer('pointermove', 100, 60));
    fire(document, pointer('pointerup', 100, 60));
    expect(onMove).toHaveBeenCalledWith('c1', 'went_well', 0);
  });

  it('skips the dragged card when counting its own column', () => {
    // Counting it would make "drop where it already is" compute an index one
    // past its own position, and every within-column drag would nudge.
    const { onMove, grip } = setup();
    fire(grip, pointer('pointerdown', 10, 10));
    fire(document, pointer('pointermove', 100, 390)); // past every card
    fire(document, pointer('pointerup', 100, 390));
    expect(onMove).toHaveBeenCalledWith('c1', 'went_well', 2);
  });

  it('treats a press that never moves as a click, not a drag', () => {
    // The grip is also the button that opens the keyboard move menu, so a
    // drag that armed on pointerdown would make that menu unreachable.
    const { onMove, grip } = setup();
    fire(grip, pointer('pointerdown', 10, 10));
    fire(document, pointer('pointermove', 12, 11)); // under the 4px threshold
    fire(document, pointer('pointerup', 12, 11));
    expect(onMove).not.toHaveBeenCalled();
    expect(screen.getByTestId('target').textContent).toBe('none');
  });

  it('cancels on Escape and says so', () => {
    const { onMove, grip } = setup();
    fire(grip, pointer('pointerdown', 10, 10));
    fire(document, pointer('pointermove', 400, 20));
    fire(document, new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));

    expect(onMove).not.toHaveBeenCalled();
    expect(screen.getByTestId('say').textContent).toBe('Move cancelled.');
  });

  it('distinguishes a drop outside any column from a cancel', () => {
    // Found in a browser, not here: releasing over the toolbar reported "Move
    // cancelled", which reads as though the board rejected the move. Nothing
    // went wrong — the card was simply released nowhere.
    const { onMove, grip } = setup();
    document.elementFromPoint = (() => document.body) as typeof document.elementFromPoint;

    fire(grip, pointer('pointerdown', 10, 10));
    fire(document, pointer('pointermove', 400, 20));
    fire(document, pointer('pointerup', 400, 20));

    expect(onMove).not.toHaveBeenCalled();
    expect(screen.getByTestId('say').textContent).toBe('Dropped outside a column. The card did not move.');
  });

  it('cancels when the browser takes the pointer away', () => {
    // pointercancel fires when the OS or browser claims the gesture — a phone
    // deciding mid-drag that it was a scroll, or a call arriving.
    const { onMove, grip } = setup();
    fire(grip, pointer('pointerdown', 10, 10));
    fire(document, pointer('pointermove', 400, 20));
    fire(document, pointer('pointercancel', 400, 20));
    expect(onMove).not.toHaveBeenCalled();
  });

  it('announces the pickup and the landing', () => {
    const { grip } = setup();
    fire(grip, pointer('pointerdown', 10, 10));
    fire(document, pointer('pointermove', 400, 20));
    expect(screen.getByTestId('say').textContent).toContain('Picked up');

    fire(document, pointer('pointerup', 400, 20));
    // Position is 1-based here: "position 0" is not a thing anyone says.
    expect(screen.getByTestId('say').textContent).toBe('Moved to demos, position 1.');
  });

  it('ignores a secondary-button press', () => {
    const { onMove, grip } = setup();
    fire(grip, new MouseEvent('pointerdown', { bubbles: true, clientX: 10, clientY: 10, button: 2 }));
    fire(document, pointer('pointermove', 400, 20));
    fire(document, pointer('pointerup', 400, 20));
    expect(onMove).not.toHaveBeenCalled();
  });

  it('removes its document listeners when the board unmounts mid-drag', () => {
    const onMove = vi.fn();
    const { unmount } = render(<Harness onMove={onMove} />);
    installLayout();
    fire(screen.getByRole('button', { name: 'grip' }), pointer('pointerdown', 10, 10));
    fire(document, pointer('pointermove', 400, 20));

    const removeSpy = vi.spyOn(document, 'removeEventListener');
    unmount();
    const removed = removeSpy.mock.calls.map(([type]) => type);
    expect(removed).toEqual(expect.arrayContaining(['pointermove', 'pointerup', 'pointercancel', 'keydown']));
  });
});
