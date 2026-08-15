/**
 * The deck, and the one thing a closed deck must always do: say why.
 *
 * Every case here corresponds to a way the old board left someone stuck — grey
 * cards with no explanation, a vote silently applied to the wrong ticket, and a
 * toggle whose label said "vote 5" when tapping it would withdraw your 5.
 */

import { render, screen } from '@testing-library/preact';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { POKER_DECK } from '../types/enums';
import { Deck } from './Deck';

function renderDeck(props: Partial<Parameters<typeof Deck>[0]> = {}) {
  const onVote = vi.fn();
  const view = render(<Deck mine="" pending={false} disabled={false} reason="" locked={false} onVote={onVote} {...props} />);
  return { ...view, onVote };
}

describe('Deck', () => {
  it('offers every server-validated card and nothing else', () => {
    renderDeck();
    const buttons = screen.getAllByRole('button');
    expect(buttons).toHaveLength(POKER_DECK.length);
    // Against the generated enum, not a hand-written list: a deck the server
    // would refuse a value from is the failure this pins.
    for (const value of POKER_DECK) {
      expect(screen.getByRole('button', { name: `Vote ${value}` })).toBeTruthy();
    }
  });

  it('labels your selected card as a withdrawal, not another vote', async () => {
    const user = userEvent.setup();
    const { onVote } = renderDeck({ mine: '5' });

    const selected = screen.getByRole('button', { name: 'Withdraw your vote of 5' });
    expect(selected.getAttribute('aria-pressed')).toBe('true');
    // Every other card still reads as a vote.
    expect(screen.getByRole('button', { name: 'Vote 8' })).toBeTruthy();

    await user.click(selected);
    expect(onVote).toHaveBeenCalledWith('5');
  });

  it.each([
    ['🔒 Voting locked by the host', 'locked'],
    ['Voting closed — waiting for the host', 'revealed'],
    ['No tickets loaded', 'empty batch'],
  ])('shows %s rather than an unexplained grey deck (%s)', (reason) => {
    renderDeck({ disabled: true, reason });
    expect(screen.getByRole('status').textContent).toBe(reason);
    for (const button of screen.getAllByRole('button')) {
      expect((button as HTMLButtonElement).disabled).toBe(true);
    }
  });

  it('explains the peek trap specifically', () => {
    // The dangerous one: a vote always applies to the LIVE ticket, so a deck
    // left open under a preview invites voting on the wrong thing entirely.
    renderDeck({ disabled: true, reason: 'Previewing a ticket — “Back to live” to vote' });
    expect(screen.getByRole('status').textContent).toContain('Back to live');
  });

  it('tells you what you voted, and how to take it back', () => {
    renderDeck({ mine: '8' });
    expect(screen.getByRole('status').textContent).toBe('Your vote: 8 — tap it again to withdraw');
  });

  it('marks an unacknowledged tap without moving anything', () => {
    // The pending style has to be non-positional: a card that jumps when the
    // server is slow reads as a bug, and on a tunnel the server is often slow.
    const { rerender } = renderDeck({ mine: '3', pending: true });
    const waiting = screen.getByRole('button', { name: 'Withdraw your vote of 3' });
    expect(waiting.className).toContain('pcardWait');

    rerender(<Deck mine="3" pending={false} disabled={false} reason="" locked={false} onVote={vi.fn()} />);
    expect(screen.getByRole('button', { name: 'Withdraw your vote of 3' }).className).not.toContain('pcardWait');
  });
});
