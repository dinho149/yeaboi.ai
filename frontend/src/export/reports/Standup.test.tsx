/**
 * The standup export.
 *
 * The behaviour worth pinning here is what moved off the Python side with the
 * render layer: the two tone lookups that used to be three colour dicts, the
 * adaptive category grid, and the count-chip pluralisation — which is exactly
 * the kind of detail a port loses silently, since "1 tickets" renders fine.
 */

import { render, screen } from '@testing-library/preact';
import { describe, expect, it } from 'vitest';

import type { StandupMember } from '../boot';
import { Standup } from './Standup';

function member(over: Partial<StandupMember> = {}): StandupMember {
  return {
    name: 'Ada Lovelace',
    summary: [{ s: 'Shipped the SSO flow.' }],
    categories: [],
    footnotes: [],
    counts: [0, 0, 0],
    links: [],
    ...over,
  };
}

const BASE = {
  sprint: { name: 'Sprint 42', day: 7, total: 10 },
  confidence: { label: 'At risk', pct: 68, text: 'At risk (68%)', trend: '', trendText: '', rationale: '' },
  summary: [],
  members: [],
  activityCounts: [] as Array<[string, number]>,
  activityWindow: '',
  coverage: [] as Array<[string, string]>,
  skipped: [] as Array<[string, string]>,
  images: [],
  trend: null,
  warnings: [],
};

describe('Standup', () => {
  it('pluralises the count chips, and leaves "code" uncountable', () => {
    const { container } = render(
      <Standup {...BASE} members={[member({ counts: [1, 3, 2] })]} />
    );
    const chips = [...container.querySelectorAll('.chips .chip')].map((c) => c.textContent);
    expect(chips).toEqual(['1 ticket', '3 code', '2 docs']);
  });

  it('shows no chip for a category with no activity', () => {
    const { container } = render(<Standup {...BASE} members={[member({ counts: [0, 0, 0] })]} />);
    expect(container.querySelectorAll('.chips .chip')).toHaveLength(0);
  });

  it('colours the confidence by its label, and falls back for an unknown one', () => {
    // Second tile: sprint day, confidence, members.
    const value = () => container.querySelector('.stat:nth-child(2) .statValue');
    const { container, rerender } = render(<Standup {...BASE} />);
    expect(value()?.getAttribute('style')).toContain('var(--warn)');

    // The engine produces these strings; it is not a validated union, so an
    // unfamiliar one must degrade rather than break the page.
    rerender(<Standup {...BASE} confidence={{ ...BASE.confidence, label: 'Whatever' }} />);
    expect(value()?.getAttribute('style')).toContain('var(--low)');
  });

  it('shows an em dash rather than a percentage when confidence is unknown', () => {
    const { container } = render(
      <Standup
        {...BASE}
        confidence={{ ...BASE.confidence, label: 'Insufficient data', pct: 0, text: 'Insufficient data' }}
      />
    );
    expect(container.querySelector('.stat:nth-child(2) .statValue')?.textContent).toBe('—');
  });

  it('puts the sprint bar inside its own tile, not under the whole grid', () => {
    // A full-width bar under four tiles reads as page progress, not "day 7 of 10".
    const { container } = render(<Standup {...BASE} />);
    const tile = container.querySelector('.stat');
    expect(tile?.querySelector('[aria-label="Sprint day 7 of 10"]')).not.toBeNull();
  });

  it('marks a blocked member and shows what they are blocked on', () => {
    const { container } = render(
      <Standup {...BASE} members={[member({ blockers: [{ s: 'waiting on review' }] })]} />
    );
    expect(container.querySelector('.blocked')).not.toBeNull();
    expect(screen.getByText('Blocker').className).toContain('chip');
    expect(container.querySelector('.note')?.textContent).toBe('Blockerwaiting on review');
  });

  it('renders ticket keys in prose as links and the rest as text', () => {
    render(
      <Standup
        {...BASE}
        members={[
          member({
            summary: [{ s: 'Shipped ' }, { s: 'ACME-101', href: 'https://x/browse/ACME-101' }, { s: '.' }],
          }),
        ]}
      />
    );
    expect(screen.getByRole('link', { name: 'ACME-101' }).getAttribute('href')).toBe('https://x/browse/ACME-101');
  });

  it('drops a link whose URL the exporter rejected, keeping the label', () => {
    const { container } = render(
      <Standup {...BASE} members={[member({ links: [['see this', '']] })]} />
    );
    expect(container.querySelector('.chipRow a')).toBeNull();
    expect(container.querySelector('.chipRow .chip')?.textContent).toBe('see this');
  });

  it('gives a quiet category a footnote instead of a column', () => {
    const { container } = render(
      <Standup
        {...BASE}
        members={[
          member({
            categories: [{ label: 'Ticketing', items: [[{ s: 'ACME-1 moved.' }]], links: [] }],
            footnotes: [{ label: 'Documentation', runs: [{ s: 'Documentation sources are not configured.' }] }],
          }),
        ]}
      />
    );
    expect([...container.querySelectorAll('.categories .eyebrow')].map((e) => e.textContent)).toEqual(['Ticketing']);
    // The wording survives: "not configured" must stay distinguishable from
    // "no activity detected".
    expect(container.querySelector('.footnote')?.textContent).toBe(
      'Documentation — Documentation sources are not configured.'
    );
  });

  it('renders a one-sentence team summary as a paragraph and several as a list', () => {
    const { container, rerender } = render(<Standup {...BASE} summary={[[{ s: 'Steady progress.' }]]} />);
    expect(container.querySelector('#summary ul')).toBeNull();
    expect(container.querySelector('#summary p')?.textContent).toBe('Steady progress.');

    rerender(<Standup {...BASE} summary={[[{ s: 'One.' }], [{ s: 'Two.' }]]} />);
    expect(container.querySelectorAll('#summary li')).toHaveLength(2);
  });

  it('scales the activity bars against the busiest member', () => {
    // Every bar filling the track would be four pictures of 100%.
    const { container } = render(
      <Standup
        {...BASE}
        members={[member({ name: 'Ada', counts: [4, 0, 0] }), member({ name: 'Bo', counts: [1, 0, 0] })]}
      />
    );
    const widths = [...container.querySelectorAll('.activityRow .segTrack')].map(
      (bar) => (bar as HTMLElement).style.width
    );
    expect(widths).toEqual(['100%', '25%']);
  });

  it('leaves an all-quiet member out of the activity bars but keeps their card', () => {
    const { container } = render(
      <Standup {...BASE} members={[member({ name: 'Ada', counts: [2, 0, 0] }), member({ name: 'Quiet' })]} />
    );
    expect([...container.querySelectorAll('.activityName')].map((n) => n.textContent)).toEqual(['Ada']);
    expect(screen.getByText('Quiet')).toBeTruthy();
  });

  it('draws no activity block at all when nobody has counts', () => {
    const { container } = render(<Standup {...BASE} members={[member()]} />);
    expect(container.querySelector('.activity')).toBeNull();
  });

  it('names each coverage status beside its dot, never colour alone', () => {
    const { container } = render(
      <Standup {...BASE} coverage={[['ticketing', 'covered'], ['documentation', 'not_configured']]} />
    );
    const entries = [...container.querySelectorAll('.coverage')].map((c) => c.textContent);
    expect(entries).toEqual(['ticketing covered', 'documentation not configured']);
    const dots = [...container.querySelectorAll('.dot')].map((d) => (d as HTMLElement).style.background);
    expect(dots).toEqual(['var(--ok)', 'var(--low)']);
  });

  it('tells a reader when there were no individual updates', () => {
    render(<Standup {...BASE} />);
    expect(screen.getByText('No individual updates.').tagName).toBe('P');
  });
});
