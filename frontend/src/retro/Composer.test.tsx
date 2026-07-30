/**
 * The single composer.
 *
 * Worth testing directly because it replaced four separate textareas with one
 * plus a destination control — so "which column does this card go to" moved
 * from being implicit in *where you typed* to explicit state, and getting it
 * wrong sends someone's card to the wrong column silently.
 */

import { render, screen } from '@testing-library/preact';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { Composer } from './Composer';

function setup(props: Partial<Parameters<typeof Composer>[0]> = {}) {
  const handlers = { onGridChange: vi.fn(), onTextChange: vi.fn(), onSubmit: vi.fn() };
  const view = render(
    <Composer
      grid="went_well"
      text=""
      locked={false}
      focusNonce={0}
      {...handlers}
      {...props}
    />
  );
  return { ...view, ...handlers };
}

describe('Composer', () => {
  it('is a radiogroup naming the destination column', () => {
    setup();
    const group = screen.getByRole('radiogroup', { name: 'Add to column' });
    expect([...group.querySelectorAll('[role="radio"]')].map((el) => el.textContent)).toEqual([
      'What went well',
      "What didn't go well",
      'Action items',
      'Demos',
    ]);
    expect(screen.getByRole('radio', { name: 'What went well' }).getAttribute('aria-checked')).toBe('true');
  });

  it('moves between columns with the arrow keys, wrapping at the ends', async () => {
    const user = userEvent.setup();
    const { onGridChange } = setup({ grid: 'went_well' });

    await user.click(screen.getByRole('radio', { name: 'What went well' }));
    await user.keyboard('{ArrowRight}');
    expect(onGridChange).toHaveBeenLastCalledWith('didnt_go_well');

    // Left from the first wraps to the last, which is what a radiogroup does
    // and what stops the first and last columns being two taps harder to reach.
    await user.keyboard('{ArrowLeft}');
    expect(onGridChange).toHaveBeenLastCalledWith('demos');
  });

  it('submits on ⌘-Enter but not on a bare Enter', async () => {
    const user = userEvent.setup();
    const { onSubmit } = setup({ text: 'something' });

    const box = screen.getByRole('textbox');
    await user.click(box);
    // Bare Enter has to insert a newline: cards are routinely multi-line, and a
    // submitting Enter would make the second line unreachable.
    await user.keyboard('{Enter}');
    expect(onSubmit).not.toHaveBeenCalled();

    await user.keyboard('{Meta>}{Enter}{/Meta}');
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it('disables Add until there is more than whitespace', () => {
    const { rerender } = setup({ text: '   ' });
    expect(screen.getByRole('button', { name: 'Add' })).toHaveProperty('disabled', true);

    rerender(
      <Composer
        grid="went_well"
        text="a real card"
        locked={false}
        focusNonce={0}
        onGridChange={vi.fn()}
        onTextChange={vi.fn()}
        onSubmit={vi.fn()}
      />
    );
    expect(screen.getByRole('button', { name: 'Add' })).toHaveProperty('disabled', false);
  });

  it('replaces itself with a notice when the host locks the board', () => {
    setup({ locked: true });
    expect(screen.queryByRole('textbox')).toBeNull();
    expect(screen.getByRole('status').textContent).toContain('locked');
  });

  it('pulls focus each time the nonce changes, including to the same column', () => {
    // A boolean would not do: pressing `+` on the column you are already
    // composing into must still bring focus back, and a flag that is already
    // true does not re-run the effect.
    const { rerender } = setup({ focusNonce: 1 });
    const box = screen.getByRole('textbox');
    expect(document.activeElement).toBe(box);

    (document.activeElement as HTMLElement).blur();
    rerender(
      <Composer
        grid="went_well"
        text=""
        locked={false}
        focusNonce={2}
        onGridChange={vi.fn()}
        onTextChange={vi.fn()}
        onSubmit={vi.fn()}
      />
    );
    expect(document.activeElement).toBe(screen.getByRole('textbox'));
  });
});
