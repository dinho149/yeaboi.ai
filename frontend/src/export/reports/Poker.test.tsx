/**
 * The poker export.
 *
 * These cover what moved off the Python side with the render layer: how a
 * skipped ticket reads, that a hostile ticket URL never becomes a live link,
 * and that the sections which have no content are absent rather than empty.
 */

import { render, screen } from '@testing-library/preact';
import { describe, expect, it } from 'vitest';

import type { PokerTicket } from '../boot';
import { Poker } from './Poker';

function ticket(over: Partial<PokerTicket> = {}): PokerTicket {
  return {
    key: 'PROJ-1',
    summary: 'Add login',
    before: null,
    final: 5,
    estimated: true,
    votes: [],
    ...over,
  };
}

describe('Poker', () => {
  it('renders a ticket row with its points and votes', () => {
    const { container } = render(
      <Poker
        tickets={[ticket({ before: 3, votes: [{ voter: 'Alex', value: '5' }, { voter: 'Sam', value: '8' }] })]}
        participants={[]}
        trend={null}
      />
    );
    const cells = [...(container.querySelector('tbody tr')?.querySelectorAll('td') ?? [])].map((c) => c.textContent);
    // Initials in the votes cell — the names ride in the accessible label.
    expect(cells).toEqual(['PROJ-1', 'Add login', '3', '5', 'A5S8']);
    expect([...container.querySelectorAll('[aria-label$="voted 5"], [aria-label$="voted 8"]')].map((v) =>
      v.getAttribute('aria-label')
    )).toEqual(['Alex voted 5', 'Sam voted 8']);
  });

  it('says "skipped" instead of showing a number the room never agreed', () => {
    const { container } = render(
      <Poker tickets={[ticket({ estimated: false, final: null })]} participants={[]} trend={null} />
    );
    expect(container.querySelector('tbody tr td:nth-child(4)')?.textContent).toBe('skipped');
  });

  it('renders an absent points value as an em dash, not a zero', () => {
    // A blank cell reads as "the export lost it" and a 0 as "worth nothing".
    const { container } = render(<Poker tickets={[ticket()]} participants={[]} trend={null} />);
    expect(container.querySelector('tbody tr td:nth-child(3)')?.textContent).toBe('—');
  });

  it('links a ticket key to its tracker', () => {
    render(
      <Poker
        tickets={[ticket({ url: 'https://x.atlassian.net/browse/PROJ-1' })]}
        participants={[]}
        trend={null}
      />
    );
    expect(screen.getByRole('link', { name: 'PROJ-1' }).getAttribute('href')).toBe(
      'https://x.atlassian.net/browse/PROJ-1'
    );
  });

  it('degrades a javascript: ticket URL to plain text', () => {
    // The Python side allowlists it too; this is the second gate, and the one
    // that survives someone hand-editing the island.
    const { container } = render(
      <Poker tickets={[ticket({ url: 'javascript:alert(1)' })]} participants={[]} trend={null} />
    );
    expect(container.querySelector('a')).toBeNull();
    expect(container.querySelector('.chip')?.textContent).toBe('PROJ-1');
  });

  it('splits an AI note into scannable bullets', () => {
    const { container } = render(
      <Poker
        tickets={[ticket({ aiNote: 'Estimate looks high. Risk is contained; scope is clear.' })]}
        participants={[]}
        trend={null}
      />
    );
    expect([...container.querySelectorAll('#ai li')].map((li) => li.textContent)).toEqual([
      'Estimate looks high.',
      'Risk is contained',
      'scope is clear.',
    ]);
  });

  it('omits the AI and duel sections when nothing has them', () => {
    const { container } = render(<Poker tickets={[ticket()]} participants={[]} trend={null} />);
    expect(container.querySelector('#ai')).toBeNull();
    expect(container.querySelector('#duels')).toBeNull();
  });

  it('keeps a duel transcript as speech, line breaks and all', () => {
    const transcript = 'Alex (voted 5) — turn 1:\nIt is a simple endpoint.';
    const { container } = render(
      <Poker
        tickets={[ticket({ duel: { low: 'Alex (5)', high: 'Sam (8)', transcript } })]}
        participants={[]}
        trend={null}
      />
    );
    expect(container.querySelector('#duels')?.textContent).toContain(transcript);
    expect(screen.getByText('Alex (5)').className).toContain('chip');
  });

  it('counts the estimated/skipped split into one bar and its matching key', () => {
    const { container } = render(
      <Poker
        tickets={[ticket(), ticket({ key: 'PROJ-2', estimated: false, final: null })]}
        participants={[]}
        trend={null}
      />
    );
    expect(container.querySelector('[aria-label="1 of 2 tickets estimated"]')).not.toBeNull();
    expect(container.querySelector('.legend')?.textContent).toBe('Estimated 1Skipped 1');
  });

  it('draws a trend once there are two runs to compare', () => {
    const { rerender, container } = render(
      <Poker tickets={[ticket()]} participants={[]} trend={{ title: 'Estimation trend', label: 'x', points: [] }} />
    );
    expect(container.querySelector('svg')).toBeNull();

    rerender(
      <Poker
        tickets={[ticket()]}
        participants={[]}
        trend={{
          title: 'Estimation trend',
          label: 'Tickets estimated — last 2 runs',
          points: [
            ['2026-06-27', 7],
            ['2026-07-25', 5],
          ],
        }}
      />
    );
    expect(screen.getByRole('img', { name: 'Tickets estimated — last 2 runs' }).tagName).toBe('svg');
    expect(screen.getByText('2026-06-27').tagName).toBe('SPAN');
  });

  it('tells a reader why an empty table is empty', () => {
    render(<Poker tickets={[]} participants={[]} trend={null} />);
    expect(screen.getByText('No tickets were brought to the session.').tagName).toBe('P');
  });
});
