/**
 * The performance exports — 1:1 prep, 1:1 summary, six-month review.
 *
 * One component draws all three, so what is worth testing is the shape it is
 * given, not each artifact. The bullet-splitting threshold is the one piece of
 * real judgement in here and it gets both sides.
 */

import { render, screen } from '@testing-library/preact';
import { describe, expect, it } from 'vitest';

import { Performance } from './Performance';

/** Comfortably over the 160-character threshold — 190. */
const LONG =
  'Ada shipped the SSO rollout across every tenant and closed the audit findings. ' +
  'The break-glass path is still untested; the follow-up work needs a dedicated spike before the review.';

describe('Performance', () => {
  it('names the engineer with an avatar', () => {
    const { container } = render(<Performance engineer="Ada Lovelace" sections={[]} warnings={[]} />);
    expect(screen.getByText('AL')).toBeTruthy();
    expect(container.textContent).toContain('Ada Lovelace');
  });

  it('renders the lead prose block whole, line breaks intact', () => {
    // Not split into bullets: this is the one field on these documents that a
    // person or the model wrote as continuous prose, and the newlines in a
    // sprint-work summary are its structure.
    const { container } = render(
      <Performance
        engineer="Ada"
        lead={{ title: 'Sprint work', text: 'shipped auth\nfixed CI' }}
        sections={[]}
        warnings={[]}
      />
    );
    expect(screen.getByRole('heading', { name: 'Sprint work' })).toBeTruthy();
    expect(container.textContent).toContain('shipped auth\nfixed CI');
    expect(container.querySelectorAll('li')).toHaveLength(0);
  });

  it('splits a long packed item but leaves a written bullet alone', () => {
    const { container } = render(
      <Performance
        engineer="Ada"
        sections={[{ id: 'talking-points', title: 'Talking points', items: [LONG, 'Discuss growth; align on goals.'] }]}
        warnings={[]}
      />
    );
    const items = [...container.querySelectorAll('li')].map((li) => li.textContent);
    expect(items).toEqual([
      'Ada shipped the SSO rollout across every tenant and closed the audit findings.',
      'The break-glass path is still untested',
      'the follow-up work needs a dedicated spike before the review.',
      // Short enough to be a bullet someone wrote — splitting on the semicolon
      // would fragment a single thought into two half-sentences.
      'Discuss growth; align on goals.',
    ]);
  });

  it('keeps split fragments in one flat list', () => {
    // A nested <ul> outside an <li> is invalid, and indents fragments of one
    // bullet as if they were a sub-topic.
    const { container } = render(
      <Performance engineer="Ada" sections={[{ id: 'points', title: 'Points', items: [LONG] }]} warnings={[]} />
    );
    expect(container.querySelectorAll('ul')).toHaveLength(1);
    expect(container.querySelectorAll('ul ul')).toHaveLength(0);
  });

  it('draws nothing for a section with no items', () => {
    const { container } = render(
      <Performance engineer="Ada" sections={[{ id: 'gaps-observed', title: 'Gaps observed', items: [] }]} warnings={[]} />
    );
    expect(container.textContent).not.toContain('Gaps observed');
  });

  it('gives every section a linkable id', () => {
    const { container } = render(
      <Performance engineer="Ada" sections={[{ id: 'areas-for-improvement', title: 'Areas for improvement', items: ['x'] }]} warnings={[]} />
    );
    expect(container.querySelector('#areas-for-improvement')).toBeTruthy();
  });

  it('shows the framework footnote when one was used', () => {
    render(<Performance engineer="Ada" sections={[]} footnote="Framework: Dreyfus" warnings={[]} />);
    expect(screen.getByText('Framework: Dreyfus')).toBeTruthy();
  });
});

describe('Performance — the numbers', () => {
  const STAT = { id: 'tests_rate', label: 'Tests alongside changes', value: 62, unit: '%', source: 'analysis', group: 'practice' };

  it('renders a percentage as a percentage and a ratio as a ratio', () => {
    const { container } = render(
      <Performance
        engineer="Ada"
        sections={[]}
        warnings={[]}
        stats={[
          STAT,
          { id: 'stories', label: 'Stories completed', value: 12, of: 14, unit: '', source: 'analysis', group: 'delivery' },
        ]}
      />
    );
    expect(container.textContent).toContain('62%');
    expect(container.textContent).toContain('12 / 14');
  });

  it('gives a ratio a bar inside its own tile, and a bare count none', () => {
    // A bar under the whole grid reads as page progress rather than "12 of 14".
    const { container } = render(
      <Performance
        engineer="Ada"
        sections={[]}
        warnings={[]}
        stats={[{ id: 'stories', label: 'Stories completed', value: 12, of: 14, unit: '', source: 'analysis', group: 'delivery' }]}
      />
    );
    expect(container.querySelectorAll('[role="img"]')).toHaveLength(1);

    const bare = render(
      <Performance engineer="Ada" sections={[]} warnings={[]} stats={[{ ...STAT, unit: 'pts', value: 34 }]} />
    );
    expect(bare.container.querySelectorAll('[role="img"]')).toHaveLength(0);
  });

  it('paints no stat, because whether a figure is good is the reader’s judgement', () => {
    const { container } = render(<Performance engineer="Ada" sections={[]} warnings={[]} stats={[STAT]} />);
    const value = container.querySelector('[class*="statValue"]') as HTMLElement | null;
    expect(value?.getAttribute('style') ?? '').toBe('');
  });

  it('degrades an unknown unit to the bare number', () => {
    const { container } = render(
      <Performance engineer="Ada" sections={[]} warnings={[]} stats={[{ ...STAT, unit: 'furlongs', value: 9 }]} />
    );
    expect(container.textContent).toContain('9');
    expect(container.textContent).not.toContain('furlongs');
  });

  it('demotes stats past the headline four to a list rather than dropping them', () => {
    const many = Array.from({ length: 6 }, (_, i) => ({ ...STAT, id: `m${i}`, label: `Metric ${i}` }));
    const { container } = render(<Performance engineer="Ada" sections={[]} warnings={[]} stats={many} />);
    expect(container.textContent).toContain('Metric 5');
  });

  it('draws no numbers section when there are no numbers', () => {
    const { container } = render(<Performance engineer="Ada" sections={[]} warnings={[]} />);
    expect(container.textContent).not.toContain('By the numbers');
  });
});

describe('Performance — coverage and evidence', () => {
  const COVERAGE = [
    { source: 'tickets', state: 'covered', detail: '7 of 7 named them.' },
    { source: 'retro', state: 'partial', detail: '3 runs, none named them.' },
  ];

  it('names each source the way the rest of the app names it', () => {
    const { container } = render(
      <Performance engineer="Ada" sections={[]} warnings={[]} coverage={[{ source: 'analysis', state: 'covered', detail: '' }]} />
    );
    expect(container.textContent).toContain('Team analysis');
  });

  it('spends a line on a gap and not on a source that explains itself', () => {
    const { container } = render(<Performance engineer="Ada" sections={[]} warnings={[]} coverage={COVERAGE} />);
    expect(container.textContent).toContain('3 runs, none named them.');
    expect(container.textContent).not.toContain('7 of 7 named them.');
  });

  it('renders evidence rows grouped by their source', () => {
    const { container } = render(
      <Performance
        engineer="Ada"
        sections={[]}
        warnings={[]}
        evidence={[
          {
            source: 'code',
            label: 'Code activity',
            note: 'capped at 1 of 41',
            items: [
              {
                kind: 'pr', key: '#91', title: 'Roll SSO out', url: 'https://example.invalid/pr/91',
                repo: 'acme/web', status: 'merged', time: '', children: [], type: '', parent: '', subtask: false, tickets: [],
              },
            ],
          },
        ]}
      />
    );
    expect(container.textContent).toContain('Code activity');
    expect(container.textContent).toContain('Roll SSO out');
    expect(container.textContent).toContain('capped at 1 of 41');
  });

  it('draws no evidence section for a group with no rows', () => {
    const { container } = render(
      <Performance engineer="Ada" sections={[]} warnings={[]} evidence={[{ source: 'code', label: 'Code', note: '', items: [] }]} />
    );
    expect(container.textContent).not.toContain('Evidence');
  });
});

describe('Performance — an empty section says which kind of empty', () => {
  it('names the reason nobody looked', () => {
    const { container } = render(
      <Performance
        engineer="Ada"
        warnings={[]}
        sections={[{ id: 'gaps', title: 'Gaps observed', items: [], state: 'not_configured', reason: 'No saved team analysis covers this engineer.' }]}
      />
    );
    expect(container.textContent).toContain('Gaps observed');
    expect(container.textContent).toContain('Not assessed.');
    expect(container.textContent).toContain('No saved team analysis covers this engineer.');
  });

  it('distinguishes found-nothing from nobody-looked', () => {
    const { container } = render(
      <Performance
        engineer="Ada"
        warnings={[]}
        sections={[{ id: 'gaps', title: 'Gaps observed', items: [], state: 'covered' }]}
      />
    );
    expect(container.textContent).toContain('None found in this period.');
  });

  it('drops a section that is empty for no stated reason', () => {
    const { container } = render(
      <Performance engineer="Ada" warnings={[]} sections={[{ id: 'gaps', title: 'Gaps observed', items: [] }]} />
    );
    expect(container.textContent).not.toContain('Gaps observed');
  });

  it('renders an unknown state rather than nothing', () => {
    const { container } = render(
      <Performance engineer="Ada" warnings={[]} sections={[{ id: 'gaps', title: 'Gaps observed', items: [], state: 'sampled' }]} />
    );
    expect(container.textContent).toContain('Not reported.');
  });
});

describe('Performance — the period', () => {
  it('names the window a review covers beside the engineer', () => {
    const { container } = render(
      <Performance engineer="Ada" sections={[]} warnings={[]} period={{ start: '2026-01-12', end: '2026-07-12' }} />
    );
    expect(container.textContent).toContain('2026-01-12');
    expect(container.textContent).toContain('2026-07-12');
  });
});
