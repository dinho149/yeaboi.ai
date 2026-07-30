/**
 * The delivery report export.
 *
 * The delivered-work breakdown is the piece worth pinning: the Python exporter
 * used to count it into a dict and hand a bar and a legend the same numbers
 * twice, and this draws both from one `countedSegments` call.
 */

import { render, screen } from '@testing-library/preact';
import { describe, expect, it } from 'vitest';

import { Reporting } from './Reporting';

const EMPTY = {
  metrics: [] as Array<[string, string]>,
  themes: [],
  highlights: [],
  items: [],
  breakdown: [] as Array<[string, number]>,
  emoji: {},
  trend: null,
  warnings: [],
};

describe('Reporting', () => {
  it('leads with the headline and the numbers', () => {
    const { container } = render(
      <Reporting
        {...EMPTY}
        headline="Shipped SSO."
        metrics={[
          ['Items delivered', '7'],
          ['Contributors', '3'],
        ]}
      />
    );
    expect(container.querySelector('blockquote')?.textContent).toBe('Shipped SSO.');
    expect([...container.querySelectorAll('#numbers .stat')].map((s) => s.textContent)).toEqual([
      '7Items delivered',
      '3Contributors',
    ]);
  });

  it('wears the emoji the host picked, per section', () => {
    const { container } = render(
      <Reporting {...EMPTY} metrics={[['x', '1']]} highlights={['y']} emoji={{ metrics: '📊' }} />
    );
    expect(container.querySelector('#numbers h2')?.textContent).toBe('📊By the numbers');
    // The slots the host left unset get no leading space, which is the whole
    // reason the emoji is its own element rather than a prefix on the string.
    expect(container.querySelector('#highlights h2')?.textContent).toBe('Highlights');
  });

  it('splits a packed highlight into scannable bullets but leaves a short one alone', () => {
    const { container } = render(
      <Reporting
        {...EMPTY}
        highlights={[
          'A'.repeat(150) + '. Landed MFA; docs refreshed.',
          'SSO live for all users. Done.',
        ]}
      />
    );
    const bullets = [...container.querySelectorAll('#highlights li')].map((li) => li.textContent);
    expect(bullets).toHaveLength(4);
    expect(bullets.slice(1)).toEqual(['Landed MFA', 'docs refreshed.', 'SSO live for all users. Done.']);
  });

  it('keeps the executive summary’s own paragraphing', () => {
    const { container } = render(<Reporting {...EMPTY} summary={'One.\nTwo.'} />);
    expect(container.querySelector('#summary .prose')?.textContent).toBe('One.\nTwo.');
  });

  it('draws the breakdown bar and its key from one count', () => {
    const { container } = render(
      <Reporting
        {...EMPTY}
        items={[{ key: 'A-1', title: 'x', status: 'Done', assignee: 'Ada' }]}
        breakdown={[
          ['Ada', 3],
          ['Ben', 1],
        ]}
      />
    );
    expect(container.querySelector('.legend')?.textContent).toBe('Ada 3Ben 1');
    expect(container.querySelectorAll('.segTrack i')).toHaveLength(2);
  });

  it('renders an unassigned item as an em dash rather than an empty cell', () => {
    const { container } = render(
      <Reporting {...EMPTY} items={[{ key: 'A-1', title: 'x', status: 'Done' }]} breakdown={[['Done', 1]]} />
    );
    expect(container.querySelector('tbody tr td:nth-child(4)')?.textContent).toBe('—');
  });

  it('omits every section it has nothing for', () => {
    const { container } = render(<Reporting {...EMPTY} />);
    expect(container.querySelectorAll('section')).toHaveLength(0);
    // An empty notice box would claim "we checked and found nothing to warn
    // you about", which is a stronger claim than the exporter makes.
    expect(container.querySelector('.notice')).toBeNull();
  });

  it('shows the notices it was given', () => {
    render(<Reporting {...EMPTY} warnings={['Sprint dates were inferred.']} />);
    expect(screen.getByText('Sprint dates were inferred.').tagName).toBe('LI');
  });
});
