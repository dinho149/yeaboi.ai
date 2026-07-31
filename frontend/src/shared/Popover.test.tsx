/**
 * The interaction contract of the popover group.
 *
 * Everything here is something the old `POPS` map + document click listener
 * either did by hand or did not do at all.
 */

import { fireEvent, render, screen } from '@testing-library/preact';
import { describe, expect, it } from 'vitest';

import { Popover, PopoverGroup } from './Popover';

function Toolbar() {
  return (
    <PopoverGroup>
      <Popover label="Music" trigger={<span aria-hidden="true">♪</span>}>
        <button type="button">play</button>
      </Popover>
      <Popover label="Timer" trigger={<span aria-hidden="true">⏱</span>}>
        <button type="button">start</button>
      </Popover>
    </PopoverGroup>
  );
}

const trigger = (name: string): HTMLElement => screen.getByRole('button', { name });

/**
 * The panel a trigger controls, found the way assistive tech would find it.
 *
 * Deliberately resolved through `aria-controls` rather than by role or test id:
 * that makes every assertion below double as a check that the association is
 * wired at all, which is the part the old markup was missing.
 */
function panel(name: string): HTMLElement {
  const id = trigger(name).getAttribute('aria-controls') ?? '';
  const el = document.getElementById(id);
  if (!el) throw new Error(`${name}: aria-controls="${id}" points at nothing`);
  return el;
}

describe('Popover', () => {
  it('starts closed and says so', () => {
    render(<Toolbar />);
    expect(trigger('Music')).toHaveProperty('ariaExpanded', 'false');
    expect(panel('Music').hidden).toBe(true);
  });

  it('opens on click and points the trigger at its panel', () => {
    render(<Toolbar />);
    fireEvent.click(trigger('Music'));

    expect(trigger('Music')).toHaveProperty('ariaExpanded', 'true');
    expect(panel('Music').hidden).toBe(false);
    // aria-controls must resolve, or a screen reader has nothing to follow.
    expect(trigger('Music').getAttribute('aria-controls')).toBe(panel('Music').id);
  });

  it('closes the previous panel when another opens', () => {
    render(<Toolbar />);
    fireEvent.click(trigger('Music'));
    fireEvent.click(trigger('Timer'));

    expect(panel('Music').hidden).toBe(true);
    expect(panel('Timer').hidden).toBe(false);
  });

  it('toggles closed when its own trigger is clicked again', () => {
    render(<Toolbar />);
    fireEvent.click(trigger('Music'));
    fireEvent.click(trigger('Music'));
    expect(panel('Music').hidden).toBe(true);
  });

  it('closes on a pointer press outside the group', () => {
    render(
      <div>
        <Toolbar />
        <main>board</main>
      </div>
    );
    fireEvent.click(trigger('Music'));
    fireEvent.pointerDown(screen.getByText('board'));
    expect(panel('Music').hidden).toBe(true);
  });

  it('stays open for a press inside its own panel', () => {
    render(<Toolbar />);
    fireEvent.click(trigger('Music'));
    fireEvent.pointerDown(screen.getByText('play'));
    expect(panel('Music').hidden).toBe(false);
  });

  it('closes on Escape and returns focus to the trigger', () => {
    // Escape used to close only the popovers and never the "modals"; focus was
    // never restored anywhere. A keyboard user was dropped at the document body.
    render(<Toolbar />);
    fireEvent.click(trigger('Music'));
    fireEvent.keyDown(document, { key: 'Escape' });

    expect(panel('Music').hidden).toBe(true);
    expect(document.activeElement).toBe(trigger('Music'));
  });
});
