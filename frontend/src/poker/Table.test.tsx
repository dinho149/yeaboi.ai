/**
 * The seats — and the invariant the whole ceremony rests on.
 *
 * Vote secrecy is enforced server-side by omitting `value` from the payload
 * while the round is open, so a client bug cannot leak a value it was never
 * sent. What a client bug *can* do is render the `voted` flag as if it were the
 * vote, which is why the first test looks for the values themselves rather than
 * just trusting the prop.
 */

import { render, screen, within } from '@testing-library/preact';
import { describe, expect, it } from 'vitest';

import { seat, shown } from '../test/pokerState';
import { Table } from './Table';

describe('Table', () => {
  it('shows who has voted and never what, while the round is open', () => {
    render(<Table votes={[seat('Ada', true), seat('Grace', false)]} revealed={false} />);

    const rows = screen.getAllByRole('listitem');
    expect(within(rows[0] as HTMLElement).getByText('Ada')).toBeTruthy();
    expect(rows[0]?.textContent).toContain('has voted');
    expect(rows[1]?.textContent).toContain('has not voted yet');
    // No deck value in any seat. Scoped to the seat list rather than the whole
    // component: the eyebrow legitimately carries the seat *count*, which
    // collides with the deck's own small numbers, and a whole-tree search would
    // have made this pass or fail on how many people happened to be present.
    const seats = screen.getByRole('list');
    for (const value of ['0', '1', '2', '3', '5', '8', '13', '21']) {
      expect(within(seats).queryByText(value)).toBeNull();
    }
  });


  it('shows the values once revealed, with each seat announced', () => {
    render(<Table votes={[shown('Ada', '3'), shown('Grace', '13')]} revealed />);
    expect(screen.getByText('3')).toBeTruthy();
    expect(screen.getByText('13')).toBeTruthy();
    const rows = screen.getAllByRole('listitem');
    expect(rows[0]?.textContent).toContain('voted 3');
    expect(rows[1]?.textContent).toContain('voted 13');
  });

  it('staggers the flip by seat, so the reveal sweeps the table', () => {
    const { container } = render(<Table votes={[shown('Ada', '3'), shown('Grace', '13')]} revealed />);
    const cards = container.querySelectorAll('.vcard');
    // The index drives `animation-delay` in CSS. Without it every card turns at
    // once, which is the silent reveal this redesign set out to fix.
    expect((cards[0] as HTMLElement).style.getPropertyValue('--i')).toBe('0');
    expect((cards[1] as HTMLElement).style.getPropertyValue('--i')).toBe('1');
  });

  it('distinguishes an empty room from a round nobody voted in', () => {
    const { rerender } = render(<Table votes={[]} revealed={false} />);
    expect(screen.getByText(/share the code to invite them/)).toBeTruthy();

    rerender(<Table votes={[]} revealed />);
    expect(screen.getByText('No votes were cast.')).toBeTruthy();
  });
});
