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
        sections={[{ title: 'Talking points', items: [LONG, 'Discuss growth; align on goals.'] }]}
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
      <Performance engineer="Ada" sections={[{ title: 'Points', items: [LONG] }]} warnings={[]} />
    );
    expect(container.querySelectorAll('ul')).toHaveLength(1);
    expect(container.querySelectorAll('ul ul')).toHaveLength(0);
  });

  it('draws nothing for a section with no items', () => {
    const { container } = render(
      <Performance engineer="Ada" sections={[{ title: 'Gaps observed', items: [] }]} warnings={[]} />
    );
    expect(container.textContent).not.toContain('Gaps observed');
  });

  it('gives every section a linkable id', () => {
    const { container } = render(
      <Performance engineer="Ada" sections={[{ title: 'Areas for improvement', items: ['x'] }]} warnings={[]} />
    );
    expect(container.querySelector('#areas-for-improvement')).toBeTruthy();
  });

  it('shows the framework footnote when one was used', () => {
    render(<Performance engineer="Ada" sections={[]} footnote="Framework: Dreyfus" warnings={[]} />);
    expect(screen.getByText('Framework: Dreyfus')).toBeTruthy();
  });
});
