/**
 * The team profile export — the block renderer.
 *
 * What is worth pinning here is everything the payload deliberately does *not*
 * carry, because the Python side used to and now cannot: the tone→colour
 * mapping, the avatar a `person` cell earns, the bar a `pct` cell draws, and
 * the guard that keeps an unknown tone out of a `style` attribute. Those are
 * the parts a payload snapshot cannot see.
 */

import { render, screen } from '@testing-library/preact';
import { describe, expect, it } from 'vitest';

import type { Block, ProfileSection } from '../boot';
import { Profile } from './Profile';

function section(blocks: Block[], over: Partial<ProfileSection> = {}): ProfileSection {
  return { id: 'velocity', title: 'Team & Velocity', blocks, ...over };
}

function draw(blocks: Block[], coverage: string[] = []) {
  return render(<Profile sections={[section(blocks)]} coverage={coverage} />);
}

describe('Profile', () => {
  it('renders nothing but a note when there are no sections', () => {
    render(<Profile sections={[]} coverage={['ignored']} />);
    expect(screen.getByText('No analysis to export yet.')).toBeTruthy();
    // The coverage notice must not appear on an empty report: "here is what we
    // could not read" above nothing at all reads as a failed run.
    expect(screen.queryByText('Coverage')).toBeNull();
  });

  it('leads with coverage, before the numbers', () => {
    const { container } = draw([{ kind: 'kv', rows: [['Team velocity', '28 pts/sprint']] }], [
      'Documentation — Failed',
    ]);
    const text = container.textContent ?? '';
    expect(text.indexOf('Documentation — Failed')).toBeLessThan(text.indexOf('28 pts/sprint'));
  });

  it('gives a section an id, so the contents links land', () => {
    const { container } = draw([{ kind: 'note', text: 'x' }]);
    expect(container.querySelector('section#velocity')).toBeTruthy();
  });

  describe('cells', () => {
    it('paints a tone from the token, never from a literal colour', () => {
      const { container } = draw([
        { kind: 'kv', rows: [['Completion rate', { t: '76%', tone: 'warn' }]] },
      ]);
      const painted = container.querySelector('dd span span') as HTMLElement;
      expect(painted.style.color).toBe('var(--warn)');
    });

    it('refuses a tone outside the vocabulary', () => {
      // The payload is JSON, so `Tone` is a compile-time promise only. An
      // unchecked value reaches `var(--…)` inside a style attribute.
      const rows: Array<[string, { t: string; tone: string }]> = [
        ['Rate', { t: '76%', tone: '; background: url(evil)' }],
      ];
      const { container } = draw([{ kind: 'kv', rows } as unknown as Block]);
      const painted = container.querySelector('dd span span') as HTMLElement;
      expect(painted.style.color).toBe('');
      expect(container.innerHTML).not.toContain('evil');
    });

    it('draws a proportion bar beside the number, not instead of it', () => {
      const { container } = draw([
        { kind: 'kv', rows: [['Completion rate', { t: '76%', pct: 76, tone: 'ok' }]] },
      ]);
      expect(screen.getByText('76%')).toBeTruthy();
      const fill = container.querySelector('[role="img"] > div') as HTMLElement;
      expect(fill.style.width).toBe('76%');
      expect(fill.style.background).toBe('var(--ok)');
    });

    it('labels the bar with what the number is a value of', () => {
      // The bar is `role="img"`; labelled "76%" alone it makes a screen reader
      // read the number twice, since it is already the adjacent text.
      draw([{ kind: 'kv', rows: [['Completion rate', { t: '76%', pct: 76 }]] }]);
      expect(screen.getByLabelText('Completion rate: 76%')).toBeTruthy();

      draw([
        {
          kind: 'table',
          headers: ['Type', 'Share'],
          rows: [['backend', { t: '55%', pct: 55 }]],
        },
      ]);
      expect(screen.getByLabelText('Share: 55%')).toBeTruthy();
    });

    it('gives a person cell its avatar, derived here rather than sent', () => {
      const { container } = draw([
        {
          kind: 'table',
          headers: ['Name', 'Delivered'],
          rows: [[{ t: 'Ada Okonjo', person: true }, '62']],
          numeric: [1],
        },
      ]);
      // Initials are computed in the bundle from the same digest the live
      // boards use, so an exported report and the board agree on the colour.
      expect(container.textContent).toContain('Ada Okonjo');
      expect(container.querySelector('[title="Ada Okonjo"]')).toBeTruthy();
    });

    it('links a cell through the chip, and drops an unsafe scheme', () => {
      const { container } = draw([
        {
          kind: 'table',
          headers: ['Practice', 'Example'],
          rows: [
            ['Testing', { t: 'ACME-5', href: 'https://jira.example.com/browse/ACME-5', note: 'Session expiry' }],
            ['Deploy', { t: 'ACME-6', href: 'javascript:alert(1)' }],
          ],
        },
      ]);
      const link = container.querySelector('a[href]') as HTMLAnchorElement;
      expect(link.getAttribute('href')).toBe('https://jira.example.com/browse/ACME-5');
      expect(container.querySelectorAll('a[href]').length).toBe(1);
      expect(screen.getByText('Session expiry')).toBeTruthy();
      // The unsafe one still renders — as text, which is the point.
      expect(screen.getByText('ACME-6')).toBeTruthy();
    });
  });

  describe('blocks', () => {
    it('titles a table without pretending the title is a column', () => {
      const { container } = draw([
        {
          kind: 'table',
          title: 'Engineering practices by member',
          headers: ['Member', 'Commits'],
          rows: [['Ada', '71']],
          numeric: [1],
        },
      ]);
      expect(container.querySelector('h3')?.textContent).toBe('Engineering practices by member');
      expect(container.querySelectorAll('th').length).toBe(2);
    });

    it('renders an ordered list only when the payload asks for one', () => {
      const items = [[{ s: 'HIGH: Add tests', strong: true }]];
      const { container: ol } = draw([{ kind: 'bullets', ordered: true, items }]);
      expect(ol.querySelector('ol')).toBeTruthy();
      const { container: ul } = draw([{ kind: 'bullets', items }]);
      expect(ul.querySelector('ul')).toBeTruthy();
      expect(ul.querySelector('ol')).toBeNull();
    });

    it('tones a callout rule and its title from the same token', () => {
      const { container } = draw([
        { kind: 'callout', tone: 'danger', title: 'Low sprint completion', text: 'Only 45%.' },
      ]);
      const box = container.querySelector('div[style]') as HTMLElement;
      expect(box.style.borderLeftColor).toBe('var(--danger)');
      expect((box.querySelector('strong') as HTMLElement).style.color).toBe('var(--danger)');
      expect(screen.getByText('Only 45%.')).toBeTruthy();
    });

    it('falls back to warn for a callout whose tone is not a tone', () => {
      const { container } = draw([
        { kind: 'callout', tone: 'chartreuse', title: 'x' } as unknown as Block,
      ]);
      expect((container.querySelector('div[style]') as HTMLElement).style.borderLeftColor).toBe('var(--warn)');
    });

    it('draws nothing for a bar with no positive counts', () => {
      const { container } = draw([{ kind: 'bar', label: 'By tool', counts: [['none', 0]] }]);
      expect(container.querySelector('[role="img"]')).toBeNull();
    });

    it('draws a bar and a matching key', () => {
      draw([
        {
          kind: 'bar',
          label: 'Task type distribution',
          counts: [
            ['backend', 55],
            ['qa', 15],
          ],
        },
      ]);
      expect(screen.getByText('backend')).toBeTruthy();
      expect(screen.getByText('15')).toBeTruthy();
    });

    it('renders a scope timeline through the shared trend card', () => {
      draw([
        {
          kind: 'trend',
          trend: {
            title: 'Sprint 4: scope per day',
            label: 'Sprint 4: scope points per day',
            points: [
              ['2026-07-06', 30],
              ['2026-07-09', 36],
            ],
          },
        },
      ]);
      expect(screen.getByText('Sprint 4: scope per day')).toBeTruthy();
      expect(screen.getByTitle('Sprint 4: scope points per day')).toBeTruthy();
    });

    it('embeds an image by its data URI', () => {
      // The one block kind the wire fixture cannot carry: a rendered chart's
      // bytes depend on the matplotlib version, so its shape is pinned here.
      const src = 'data:image/png;base64,iVBORw0KGgo=';
      const { container } = draw([{ kind: 'image', src, alt: 'Sprint velocity' }]);
      const img = container.querySelector('img') as HTMLImageElement;
      expect(img.getAttribute('src')).toBe(src);
      expect(img.getAttribute('alt')).toBe('Sprint velocity');
    });
  });
});
