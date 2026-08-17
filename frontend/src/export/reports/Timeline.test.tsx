/**
 * The activity timeline.
 *
 * What is worth pinning: the placement rules for awkward data (undated rows,
 * unknown kinds, empty window bounds), the clustering sweep, and the anchor
 * contract with the member cards — each behaviour that would fail silently
 * as a shifted or missing dot rather than a thrown error.
 */

import { fireEvent, render } from '@testing-library/preact';
import { describe, expect, it } from 'vitest';
import { axe } from 'vitest-axe';

import type { EvidenceItem, StandupMember } from '../boot';
import { memberSlug, Timeline } from './Timeline';

function evidence(over: Partial<EvidenceItem> = {}): EvidenceItem {
  return {
    kind: 'commit',
    key: '78e4201d',
    title: 'Fix login redirect',
    url: 'https://g/c1',
    repo: 'yeaboi/web',
    status: '',
    time: '2026-07-13T09:15:00',
    children: [],
    type: '',
    parent: '',
    subtask: false,
    tickets: [],
    ...over,
  };
}

function member(rows: EvidenceItem[], name = 'Ada Lovelace'): StandupMember {
  return {
    name,
    summary: [],
    categories: [{ label: 'Code', items: [], links: [], evidence: rows }],
    footnotes: [],
    counts: [0, rows.length, 0],
    links: [],
  };
}

const WINDOW = { start: '2026-07-13T00:00:00', end: '2026-07-13T18:00:00' };

describe('Timeline', () => {
  it('renders nothing when no evidence carries a parseable time', () => {
    const { container } = render(
      <Timeline
        members={[member([evidence({ time: '' }), evidence({ key: 'x', url: 'https://g/x', time: 'not a date' })])]}
        window={WINDOW}
      />
    );
    expect(container.firstChild).toBeNull();
  });

  it('excludes undated rows from the plot and counts them into the +N note', () => {
    const { container } = render(
      <Timeline
        members={[member([evidence(), evidence({ kind: 'wip', key: 'YB-9', url: 'https://j/YB-9', time: '' })])]}
        window={WINDOW}
      />
    );
    expect(container.querySelectorAll('.mark')).toHaveLength(1);
    expect(container.querySelector('.undated')?.textContent).toBe('+1 undated');
  });

  it('links every dot to the member card anchor the jump strip uses', () => {
    const { container } = render(<Timeline members={[member([evidence()])]} window={WINDOW} />);
    expect(container.querySelector('.mark')?.getAttribute('href')).toBe(`#m-${memberSlug('Ada Lovelace')}`);
    expect(memberSlug('Ada Lovelace')).toBe('ada-lovelace');
  });

  it('renders an unknown kind with the fallback glyph and label instead of throwing', () => {
    const { container } = render(
      <Timeline members={[member([evidence({ kind: 'weird-new-kind' })])]} window={WINDOW} />
    );
    const dot = container.querySelector('.mark');
    expect(dot).not.toBeNull();
    // The aria-label leads with the kind word — the raw kind, per kindMeta's fallback.
    expect(dot?.getAttribute('aria-label')).toContain('weird-new-kind');
  });

  it('hoists PR child commits into their own dots', () => {
    const pr = evidence({
      kind: 'pr',
      key: '#91',
      url: 'https://g/pr/91',
      time: '2026-07-13T12:00:00',
      children: [evidence({ key: 'aaa1', url: 'https://g/aaa1', time: '2026-07-13T08:00:00' })],
    });
    const { container } = render(<Timeline members={[member([pr])]} window={WINDOW} />);
    expect(container.querySelectorAll('.mark')).toHaveLength(2);
  });

  it('merges near-simultaneous events into one dot with a count badge, and keeps distant ones apart', () => {
    const twoClose = [
      evidence({ time: '2026-07-13T09:15:00' }),
      evidence({ key: 'bbb2', url: 'https://g/c2', time: '2026-07-13T09:15:40' }),
    ];
    const { container, rerender } = render(<Timeline members={[member(twoClose)]} window={WINDOW} />);
    expect(container.querySelectorAll('.mark')).toHaveLength(1);
    expect(container.querySelector('.markBadge')?.textContent).toBe('×2');

    const twoFar = [
      evidence({ time: '2026-07-13T09:15:00' }),
      evidence({ key: 'bbb2', url: 'https://g/c2', time: '2026-07-13T15:15:00' }),
    ];
    rerender(<Timeline members={[member(twoFar)]} window={WINDOW} />);
    expect(container.querySelectorAll('.mark')).toHaveLength(2);
    expect(container.querySelector('.markBadge')).toBeNull();
  });

  it('derives the axis from the event extent when the window is empty', () => {
    const { container } = render(
      <Timeline members={[member([evidence()])]} window={{ start: '', end: '' }} />
    );
    // The axis still renders ticks, and the dot sits mid-track (2% pad + ±1h).
    expect(container.querySelectorAll('.tick').length).toBeGreaterThan(0);
    const left = parseFloat((container.querySelector('.mark') as HTMLElement).style.left);
    expect(left).toBeGreaterThan(40);
    expect(left).toBeLessThan(60);
  });

  it('switches to day ticks with lane hairlines on a multi-day window', () => {
    const { container } = render(
      <Timeline
        members={[
          member([
            evidence({ time: '2026-07-10T09:00:00' }),
            evidence({ key: 'bbb2', url: 'https://g/c2', time: '2026-07-13T15:00:00' }),
          ]),
        ]}
        window={{ start: '2026-07-10T00:00:00', end: '2026-07-13T18:00:00' }}
      />
    );
    const ticks = [...container.querySelectorAll('.tick')].map((t) => t.textContent);
    // Local-midnight ticks labelled with the weekday, e.g. "Sat 07-11".
    expect(ticks.some((label) => /^(Sun|Mon|Tue|Wed|Thu|Fri|Sat) \d{2}-\d{2}$/.test(label ?? ''))).toBe(true);
    expect(container.querySelectorAll('.dayBreak').length).toBeGreaterThan(0);
  });

  it('plots events that fall outside the window instead of clipping them', () => {
    const { container } = render(
      <Timeline
        members={[member([evidence({ time: '2026-07-12T09:00:00' })])]}
        window={WINDOW}
      />
    );
    const left = parseFloat((container.querySelector('.mark') as HTMLElement).style.left);
    expect(left).toBeGreaterThanOrEqual(0);
    expect(left).toBeLessThanOrEqual(100);
  });

  it('dims other kinds when a legend key is pressed, and releases on the second press', () => {
    const { container } = render(
      <Timeline
        members={[
          member([
            evidence(),
            evidence({ kind: 'pr', key: '#91', url: 'https://g/pr/91', time: '2026-07-13T15:00:00' }),
          ]),
        ]}
        window={WINDOW}
      />
    );
    const commitKey = [...container.querySelectorAll('.legendItem')].find((b) => b.textContent === 'commit');
    fireEvent.click(commitKey as Element);
    expect(commitKey?.getAttribute('aria-pressed')).toBe('true');
    expect(container.querySelectorAll('.markSlot.dimmed')).toHaveLength(1);
    fireEvent.click(commitKey as Element);
    expect(container.querySelectorAll('.markSlot.dimmed')).toHaveLength(0);
  });

  it('renders hostile strings as text', () => {
    const hostile = '<img src=x onerror=alert(1)>';
    const { container } = render(
      <Timeline
        members={[member([evidence({ title: hostile, repo: hostile, status: hostile })], hostile)]}
        window={WINDOW}
      />
    );
    expect(container.querySelector('img')).toBeNull();
    expect(container.textContent).toContain(hostile);
  });

  it('never shows a doc row machine id — the title is the handle', () => {
    const { container } = render(
      <Timeline
        members={[
          member([evidence({ kind: 'page', key: '1892385692', title: 'MFA Runbook', url: 'https://c/p' })]),
        ]}
        window={WINDOW}
      />
    );
    expect(container.textContent).not.toContain('1892385692');
    expect(container.querySelector('.mark')?.getAttribute('aria-label')).toContain('MFA Runbook');
  });

  it('has no axe violations on a populated timeline', async () => {
    const { container } = render(
      <Timeline
        members={[
          member([
            evidence(),
            evidence({ kind: 'pr', key: '#91', url: 'https://g/pr/91', time: '2026-07-13T15:00:00' }),
            evidence({ kind: 'wip', key: 'YB-9', url: 'https://j/YB-9', time: '' }),
          ]),
        ]}
        window={WINDOW}
      />
    );
    expect(await axe(container)).toHaveNoViolations();
  });
});
