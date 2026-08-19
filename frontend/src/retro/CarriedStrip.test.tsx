/**
 * The carried-over review strip.
 *
 * Two behaviours that are easy to get subtly wrong: when it folds itself away,
 * and whether it stays folded once you have deliberately reopened it.
 */

import { render, screen } from '@testing-library/preact';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { card } from '../test/retroState';
import { CarriedStrip } from './CarriedStrip';

const item = (status: string) => card({ status: status as never, origin: 'carryover' });

describe('CarriedStrip', () => {
  it('renders nothing at all for a first-ever retro', () => {
    const { container } = render(<CarriedStrip items={[]} locked={false} onSetStatus={vi.fn()} />);
    expect(container.textContent).toBe('');
  });

  it('opens expanded and reports progress while items are unreviewed', () => {
    render(<CarriedStrip items={[item('done'), item('pending')]} locked={false} onSetStatus={vi.fn()} />);
    expect(screen.getByRole('button', { name: /Last retro/ }).getAttribute('aria-expanded')).toBe('true');
    expect(screen.getByText('1/2 reviewed')).toBeTruthy();
  });

  it('folds itself away once every item has a status', () => {
    // The point at which the review is genuinely finished — not a timer, and
    // not a guess. Until then it is the first thing the ceremony does.
    render(<CarriedStrip items={[item('done'), item('carried_over')]} locked={false} onSetStatus={vi.fn()} />);
    expect(screen.getByRole('button', { name: /Last retro/ }).getAttribute('aria-expanded')).toBe('false');
    expect(screen.getByText('2/2 reviewed')).toBeTruthy();
  });

  it('stays open after you reopen a finished strip', async () => {
    const user = userEvent.setup();
    const items = [item('done')];
    const { rerender } = render(<CarriedStrip items={items} locked={false} onSetStatus={vi.fn()} />);

    const toggle = screen.getByRole('button', { name: /Last retro/ });
    await user.click(toggle);
    expect(toggle.getAttribute('aria-expanded')).toBe('true');

    // A snapshot lands. Without the auto-collapse latch this would snap shut
    // again — the strip would fight anyone trying to look back at it.
    rerender(<CarriedStrip items={[...items]} locked={false} onSetStatus={vi.fn()} />);
    expect(screen.getByRole('button', { name: /Last retro/ }).getAttribute('aria-expanded')).toBe('true');
  });

  it('reports a status change with the item it belongs to', async () => {
    const user = userEvent.setup();
    const onSetStatus = vi.fn();
    const one = item('pending');
    render(<CarriedStrip items={[one]} locked={false} onSetStatus={onSetStatus} />);

    await user.selectOptions(screen.getByRole('combobox'), 'in_progress');
    expect(onSetStatus).toHaveBeenCalledWith(one.id, 'in_progress');
  });

  it('names each select after the item it governs', () => {
    // A row of unlabelled `<select>`s reads as "combo box, combo box, combo
    // box" — the status is meaningless without knowing whose it is.
    render(<CarriedStrip items={[card({ text: 'Add staging alerts', status: 'pending' })]} locked={false} onSetStatus={vi.fn()} />);
    expect(screen.getByRole('combobox', { name: 'Status for: Add staging alerts' })).toBeTruthy();
  });

  it('disables the selects while the board is locked', () => {
    render(<CarriedStrip items={[item('pending')]} locked onSetStatus={vi.fn()} />);
    expect(screen.getByRole('combobox')).toHaveProperty('disabled', true);
  });
});
