/**
 * The column's own composer.
 *
 * Worth testing directly because it is where "which column does this card go
 * to" is answered. That used to be explicit state on a segmented control, and
 * getting it wrong sent someone's card to the wrong column silently; it is now
 * implicit in *which box you typed in*, which is only true as long as the box
 * really does belong to its column — hence the label assertions here and the
 * end-to-end one in App.test.tsx.
 *
 * The harness mirrors Column's ownership of `open`/`focusNonce` exactly, since
 * the focus behaviour is a property of the pair rather than of either half.
 */

import { render, screen } from '@testing-library/preact';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { ColumnComposer } from './ColumnComposer';

function setup() {
  const handlers = { onSubmit: vi.fn(), onTyping: vi.fn() };

  function Harness() {
    const [open, setOpen] = useState(false);
    const [focusNonce, setFocusNonce] = useState(0);
    return (
      <ColumnComposer
        label="Demos"
        open={open}
        focusNonce={focusNonce}
        onOpen={() => {
          setOpen(true);
          setFocusNonce((n) => n + 1);
        }}
        onClose={() => setOpen(false)}
        {...handlers}
      />
    );
  }

  const view = render(<Harness />);
  return { ...view, ...handlers, user: userEvent.setup() };
}

const box = () => screen.getByRole('textbox', { name: 'Add a card to Demos' });

describe('ColumnComposer', () => {
  it('is a quiet invitation until you take it, and names its own column', async () => {
    const { user } = setup();
    expect(screen.queryByRole('textbox')).toBeNull();

    await user.click(screen.getByRole('button', { name: 'Add a card to Demos' }));
    // Focused on open: the click that asked for a writing surface should not
    // then need a second click to write in it.
    expect(document.activeElement).toBe(box());
  });

  it('submits on ⌘-Enter but not on a bare Enter', async () => {
    const { user, onSubmit } = setup();
    await user.click(screen.getByRole('button', { name: 'Add a card to Demos' }));

    // Bare Enter has to insert a newline: cards are routinely multi-line, and a
    // submitting Enter would make the second line unreachable.
    await user.type(box(), 'two{Enter}lines');
    expect(onSubmit).not.toHaveBeenCalled();

    await user.keyboard('{Meta>}{Enter}{/Meta}');
    expect(onSubmit).toHaveBeenCalledWith('two\nlines');
  });

  it('stays open and focused after adding, ready for the next card', async () => {
    const { user, onSubmit } = setup();
    await user.click(screen.getByRole('button', { name: 'Add a card to Demos' }));
    await user.type(box(), 'first');
    await user.click(screen.getByRole('button', { name: 'Add' }));

    // A retro is written in bursts. Re-opening the box between each card was
    // the whole cost of the change, so this is the case that pays for it.
    expect(onSubmit).toHaveBeenCalledWith('first');
    expect(box()).toHaveProperty('value', '');
    expect(document.activeElement).toBe(box());

    await user.type(box(), 'second');
    await user.keyboard('{Control>}{Enter}{/Control}');
    expect(onSubmit).toHaveBeenLastCalledWith('second');
  });

  it('disables Add until there is more than whitespace', async () => {
    const { user } = setup();
    await user.click(screen.getByRole('button', { name: 'Add a card to Demos' }));
    expect(screen.getByRole('button', { name: 'Add' })).toHaveProperty('disabled', true);

    await user.type(box(), '   ');
    expect(screen.getByRole('button', { name: 'Add' })).toHaveProperty('disabled', true);

    await user.type(box(), 'a real card');
    expect(screen.getByRole('button', { name: 'Add' })).toHaveProperty('disabled', false);
  });

  it('collapses on Escape and on Cancel, discarding the draft', async () => {
    const { user, onSubmit } = setup();

    await user.click(screen.getByRole('button', { name: 'Add a card to Demos' }));
    await user.type(box(), 'never mind{Escape}');
    expect(screen.queryByRole('textbox')).toBeNull();

    await user.click(screen.getByRole('button', { name: 'Add a card to Demos' }));
    expect(box()).toHaveProperty('value', '');

    await user.type(box(), 'nor this');
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('textbox')).toBeNull();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('reports typing, so peers get the ghost card in this column', async () => {
    const { user, onTyping } = setup();
    await user.click(screen.getByRole('button', { name: 'Add a card to Demos' }));
    await user.type(box(), 'ab');
    expect(onTyping).toHaveBeenCalledTimes(2);
  });
});
