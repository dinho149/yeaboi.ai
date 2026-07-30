/**
 * The retro export.
 *
 * The two behaviours worth pinning here both moved off the Python side: empty
 * columns collapsing to footnotes, and the columns wearing the *live board's*
 * colours rather than a second set that could drift from them.
 */

import { render, screen } from '@testing-library/preact';
import { describe, expect, it } from 'vitest';

import type { RetroColumn } from '../boot';
import { Retro } from './Retro';

function column(over: Partial<RetroColumn> = {}): RetroColumn {
  return { grid: 'went_well', cards: [], ...over };
}

/** All four, in board order — which is what the exporter always sends. */
function board(cards: Partial<Record<RetroColumn['grid'], RetroColumn['cards']>>): RetroColumn[] {
  return (['went_well', 'didnt_go_well', 'action_items', 'demos'] as const).map((grid) =>
    column({ grid, cards: cards[grid] ?? [] })
  );
}

describe('Retro', () => {
  it('renders a card with its author and reactions', () => {
    const { container } = render(
      <Retro
        columns={board({
          went_well: [{ text: 'fast deploys', author: 'Sam', reactions: [['👍', 3]] }],
        })}
        participants={[]}
        carried={[]}
        trend={null}
      />
    );
    const item = container.querySelector('li');
    expect(item?.textContent).toBe('fast deploys — SSam👍 3');
    // "S" is the avatar; the name is beside it, not replaced by it.
    expect(item?.querySelector('.avatar')?.textContent).toBe('S');
  });

  it('attributes a facilitator card to the model, not to a person', () => {
    const { container } = render(
      <Retro
        columns={board({ action_items: [{ text: 'add retry guard', ai: true, reactions: [] }] })}
        participants={[]}
        carried={[]}
        trend={null}
      />
    );
    expect(container.querySelector('li')?.textContent).toBe('add retry guard (AI)');
    expect(container.querySelector('li .avatar')).toBeNull();
  });

  it('collapses an empty column to a footnote instead of an empty card', () => {
    const { container } = render(
      <Retro
        columns={board({ went_well: [{ text: 'fast deploys', reactions: [] }] })}
        participants={[]}
        carried={[]}
        trend={null}
      />
    );
    expect([...container.querySelectorAll('h3')].map((h) => h.textContent)).toEqual(['What went well1']);
    expect([...container.querySelectorAll('p')].map((p) => p.textContent)).toEqual([
      "What didn't go well — no cards.",
      'Action items — no cards.',
      'Demos — no cards.',
    ]);
  });

  it('gives each column the live board’s own colour', () => {
    // Imported from gridTone.ts, the same map Column.tsx reads. A second table
    // here is how "didn't go well" ends up amber in the meeting and red in the
    // file, which is enough to make someone read the wrong column.
    const { container } = render(
      <Retro
        columns={board({
          went_well: [{ text: 'a', reactions: [] }],
          didnt_go_well: [{ text: 'b', reactions: [] }],
          action_items: [{ text: 'c', reactions: [] }],
          demos: [{ text: 'd', reactions: [] }],
        })}
        participants={[]}
        carried={[]}
        trend={null}
      />
    );
    const tones = [...container.querySelectorAll('[style*="--col-tone"]')].map((el) =>
      (el as HTMLElement).style.getPropertyValue('--col-tone')
    );
    expect(tones).toEqual(['var(--ok)', 'var(--warn)', 'var(--accent)', 'var(--accent2)']);
  });

  it('keeps the column bar in board order, not count order', () => {
    // `countedSegments` would sort these descending. Correct for an anonymous
    // breakdown; wrong here, because a bar whose stripes move between two
    // retros cannot be compared with the last one.
    const { container } = render(
      <Retro
        columns={board({
          went_well: [{ text: 'a', reactions: [] }],
          action_items: [
            { text: 'b', reactions: [] },
            { text: 'c', reactions: [] },
          ],
        })}
        participants={[]}
        carried={[]}
        trend={null}
      />
    );
    expect(container.querySelector('.legend')?.textContent).toBe('What went well 1Action items 2');
  });

  it('draws no bar for a board nobody wrote on', () => {
    const { container } = render(<Retro columns={board({})} participants={[]} carried={[]} trend={null} />);
    expect(container.querySelector('.segTrack')).toBeNull();
    expect(container.querySelectorAll('p')).toHaveLength(4);
  });

  it('labels a carried item with its status', () => {
    render(
      <Retro
        columns={board({})}
        participants={[]}
        carried={[{ status: 'carried_over', text: 'ship the docs' }]}
        trend={null}
      />
    );
    expect(screen.getByText('[Carried Over]').tagName).toBe('STRONG');
  });

  it('lists participants by name', () => {
    const { container } = render(
      <Retro columns={board({})} participants={['Sam', 'Rae']} carried={[]} trend={null} />
    );
    expect(container.querySelector('.people')?.textContent).toBe('Participants:SSamRRae');
  });
});
