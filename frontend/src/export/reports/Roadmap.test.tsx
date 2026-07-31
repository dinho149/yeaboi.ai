/**
 * The roadmap export.
 *
 * These cover the behaviour that moved off the Python side with the render
 * layer: the size and quarter mixes, which the exporter used to count into a
 * dict and hand to a bar and a legend separately.
 */

import { render, screen } from '@testing-library/preact';
import { describe, expect, it } from 'vitest';

import type { RoadmapProject } from '../boot';
import { Roadmap } from './Roadmap';

function project(over: Partial<RoadmapProject> = {}): RoadmapProject {
  return { index: 1, name: 'SSO rollout', size: 'small', ...over };
}

describe('Roadmap', () => {
  it('lists projects with their size, quarter and themes', () => {
    const { container } = render(
      <Roadmap
        summary=""
        projects={[project({ size: 'large', quarter: 'Q3 2026', themes: ['Security', 'Platform'] })]}
        warnings={[]}
      />
    );
    // Chips only: "Large" and "Q3 2026" also appear in the mix legends above,
    // which is correct — the same word means the same thing in both places.
    const chips = [...container.querySelectorAll('.chips .chip')].map((c) => c.textContent);
    expect(chips).toEqual(['Large', 'Q3 2026', 'Security', 'Platform']);
  });

  it('treats any size that is not "large" as small', () => {
    // The engine's own rule. A project whose size the LLM returned as "" or
    // "medium" should read as small, not as an unlabelled card.
    const { container } = render(<Roadmap summary="" projects={[project({ size: '' })]} warnings={[]} />);
    expect(container.querySelector('.chips .chip')?.textContent).toBe('Small');
  });

  it('splits a packed description into scannable bullets', () => {
    const { container } = render(
      <Roadmap
        summary=""
        projects={[project({ description: 'Ship SSO for all tenants. Cover break-glass; add audit logs.' })]}
        warnings={[]}
      />
    );
    expect([...container.querySelectorAll('li')].map((li) => li.textContent)).toEqual([
      'Ship SSO for all tenants.',
      'Cover break-glass',
      'add audit logs.',
    ]);
  });

  it('counts the size mix into one bar and its matching key', () => {
    const { container } = render(
      <Roadmap
        summary=""
        projects={[project({ size: 'large' }), project({ index: 2, size: 'small' })]}
        warnings={[]}
      />
    );
    const bar = container.querySelector('[aria-label="Projects by size"]');
    expect(bar).toBeTruthy();
    // The bar and its key are built from one pass, so a segment per label.
    expect(bar?.children).toHaveLength(2);
    expect(screen.getAllByText('Large').length).toBeGreaterThan(0);
  });

  it('draws no quarter bar when every project lands in one quarter', () => {
    // A single-value distribution is a solid block captioned with the only
    // value it could have — it looks like a chart and carries nothing.
    const { container } = render(
      <Roadmap
        summary=""
        projects={[project({ quarter: 'Q3 2026' }), project({ index: 2, quarter: 'Q3 2026' })]}
        warnings={[]}
      />
    );
    expect(container.querySelector('[aria-label="Projects by quarter"]')).toBeNull();
  });

  it('draws the quarter bar once there are two', () => {
    const { container } = render(
      <Roadmap
        summary=""
        projects={[project({ quarter: 'Q3 2026' }), project({ index: 2, quarter: 'Q4 2026' })]}
        warnings={[]}
      />
    );
    expect(container.querySelector('[aria-label="Projects by quarter"]')).toBeTruthy();
  });

  it('says so when the roadmap yielded nothing', () => {
    render(<Roadmap summary="" projects={[]} warnings={[]} />);
    expect(screen.getByText(/No projects were extracted/)).toBeTruthy();
  });

  it('renders the rationale behind a "Why now" label', () => {
    render(<Roadmap summary="" projects={[project({ rationale: 'Security deadline.' })]} warnings={[]} />);
    expect(screen.getByText('Why now')).toBeTruthy();
    expect(screen.getByText(/Security deadline/)).toBeTruthy();
  });
});
