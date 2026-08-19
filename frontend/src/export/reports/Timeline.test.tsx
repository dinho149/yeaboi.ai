/**
 * The activity timeline.
 *
 * What is worth pinning, in order: that the board **says something without a
 * pointer** — the captions are the whole reason this component was rebuilt, and
 * v1 passed a full suite while rendering a row of unlabelled circles. Then the
 * placement rules for awkward data (undated rows, unknown kinds, empty window
 * bounds), the two tiers, the cross-lane threads, and the anchor contract with
 * the member cards — each a behaviour that fails silently as a shifted, missing
 * or mute mark rather than a thrown error.
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

function member(rows: EvidenceItem[], name = 'Ada Lovelace', over: Partial<StandupMember> = {}): StandupMember {
  return {
    name,
    summary: [],
    categories: [{ label: 'Code', items: [], links: [], evidence: rows }],
    footnotes: [],
    counts: [0, rows.length, 0],
    links: [],
    ...over,
  };
}

const WINDOW = { start: '2026-07-13T00:00:00', end: '2026-07-13T18:00:00' };
/** Both tiers at once — a landmark disc or a minor stroke. */
const marks = (c: Element) => c.querySelectorAll('.tlDot, .tlMinor');

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

  /* ---- the point of the whole component -------------------------------- */

  it('prints a landmark caption on the board, not only in a tooltip', () => {
    const { container } = render(
      <Timeline
        members={[
          member([
            evidence({
              kind: 'pr',
              key: '#91',
              title: 'Fix login redirect',
              url: 'https://github.com/acme/web/pull/91',
              status: 'merged',
              time: '2026-07-13T14:05:00',
            }),
          ]),
        ]}
        window={WINDOW}
      />
    );
    const captions = [...container.querySelectorAll('.tlCap')].map((node) => node.textContent);
    expect(captions).toContain('#91 merged');
  });

  it('captions a burst of commits with its count, and a lone one with its sha', () => {
    const burst = [
      evidence({ key: 'aaa1', url: 'https://g/a1', time: '2026-07-13T09:00:00' }),
      evidence({ key: 'bbb2', url: 'https://g/b2', time: '2026-07-13T09:05:00' }),
      evidence({ key: 'ccc3', url: 'https://g/c3', time: '2026-07-13T09:10:00' }),
    ];
    const { container, rerender } = render(<Timeline members={[member(burst)]} window={WINDOW} />);
    let captions = [...container.querySelectorAll('.tlCap')].map((node) => node.textContent);
    expect(captions).toEqual(['3 commits']);
    // Three separate strokes at their true positions — the count is the caption,
    // not a merged dot that hides where the work fell.
    expect(container.querySelectorAll('.tlMinor')).toHaveLength(3);

    rerender(<Timeline members={[member([burst[0] as EvidenceItem])]} window={WINDOW} />);
    captions = [...container.querySelectorAll('.tlCap')].map((node) => node.textContent);
    expect(captions).toEqual(['aaa1']);
  });

  it('splits a run across a compressed stretch instead of counting through it', () => {
    // Two bursts either side of a five-hour gap: two captions, not one "4 commits".
    const { container } = render(
      <Timeline
        members={[
          member([
            evidence({ key: 'a1', url: 'https://g/a1', time: '2026-07-13T09:00:00' }),
            evidence({ key: 'a2', url: 'https://g/a2', time: '2026-07-13T09:05:00' }),
            evidence({ key: 'b1', url: 'https://g/b1', time: '2026-07-13T15:00:00' }),
            evidence({ key: 'b2', url: 'https://g/b2', time: '2026-07-13T15:05:00' }),
          ]),
        ]}
        window={WINDOW}
      />
    );
    const captions = [...container.querySelectorAll('.tlCap')].map((node) => node.textContent);
    expect(captions).toEqual(['2 commits', '2 commits']);
  });

  it('drops a caption that would land on its neighbour but keeps the mark', () => {
    // A minute apart: far enough not to share a disc, too close to label twice.
    const { container } = render(
      <Timeline
        members={[
          member([
            evidence({ kind: 'issue', key: 'YB-1', url: 'https://j/1', status: 'Done', time: '2026-07-13T09:00:00' }),
            evidence({
              kind: 'issue',
              key: 'YB-2',
              url: 'https://j/2',
              status: 'In Progress',
              time: '2026-07-13T09:01:00',
            }),
          ]),
        ]}
        window={WINDOW}
      />
    );
    expect(container.querySelectorAll('.tlDot')).toHaveLength(2);
    expect(container.querySelectorAll('.tlCap')).toHaveLength(1);
  });

  /* ---- the rail --------------------------------------------------------- */

  it('states the day’s shape in the rail: span, count and per-kind tally', () => {
    const { container } = render(
      <Timeline
        members={[
          member([
            evidence({ time: '2026-07-13T09:12:00' }),
            evidence({ kind: 'pr', key: '#91', url: 'https://g/pr/91', time: '2026-07-13T16:45:00' }),
            evidence({ kind: 'wip', key: 'YB-9', url: 'https://j/YB-9', time: '' }),
          ]),
        ]}
        window={WINDOW}
      />
    );
    expect(container.querySelector('.tlSpan')?.textContent).toBe('09:12 → 16:45 · 2 events');
    expect(container.querySelector('.tlTally')?.textContent).toContain('1');
    expect(container.querySelector('.tlUndated')?.textContent).toBe('+1 undated');
    expect(container.querySelector('.tlWord')?.textContent).toBe('Ada Lovelace');
  });

  it('keeps a member whose evidence is all undated, with an empty track', () => {
    // Jira and AzDO both ship carried WIP with an empty timestamp, so a
    // tracker-heavy day can leave someone with nothing datable. Dropping their
    // lane would quietly shrink the team the overview appears to describe.
    const { container } = render(
      <Timeline
        members={[
          member([evidence()], 'Ada Lovelace'),
          member([evidence({ kind: 'wip', key: 'YB-9', url: 'https://j/YB-9', time: '' })], 'Grace Hopper'),
        ]}
        window={WINDOW}
      />
    );
    expect([...container.querySelectorAll('.tlWord')].map((n) => n.textContent)).toEqual([
      'Ada Lovelace',
      'Grace Hopper',
    ]);
    expect(container.querySelectorAll('.tlTrackEmpty')).toHaveLength(1);
    const rails = container.querySelectorAll('.tlRail');
    expect(rails[1]?.querySelector('.tlSpan')?.textContent).toBe('no times recorded');
    expect(rails[1]?.querySelector('.tlUndated')?.textContent).toBe('+1 undated');
    // Only the dated lane carries marks.
    expect(marks(container)).toHaveLength(1);
  });

  it('flags a blocked member so the eye lands on the lane that needs attention', () => {
    const { container, rerender } = render(
      <Timeline
        members={[member([evidence()], 'Ada Lovelace', { blockers: [{ s: 'Staging is down' }] })]}
        window={WINDOW}
      />
    );
    expect(container.querySelector('.tlFlag')).not.toBeNull();
    rerender(<Timeline members={[member([evidence()])]} window={WINDOW} />);
    expect(container.querySelector('.tlFlag')).toBeNull();
  });

  /* ---- the compressed axis --------------------------------------------- */

  it('compresses a quiet stretch, labels it, and says the axis is compressed', () => {
    const { container } = render(
      <Timeline
        members={[
          member([
            evidence({ time: '2026-07-13T09:00:00' }),
            evidence({ key: 'b2', url: 'https://g/b2', time: '2026-07-13T15:00:00' }),
          ]),
        ]}
        window={{ start: '2026-07-13T09:00:00', end: '2026-07-13T15:00:00' }}
      />
    );
    expect(container.querySelector('.tlNote')?.textContent).toContain('compressed');
    expect([...container.querySelectorAll('.tlGapLabel')].map((n) => n.textContent)).toEqual(['5h 40m']);
    expect(container.querySelectorAll('.tlGap').length).toBeGreaterThan(0);
  });

  it('says nothing about compression when nothing was compressed', () => {
    const { container } = render(
      <Timeline
        members={[
          member([
            evidence({ time: '2026-07-13T09:00:00' }),
            evidence({ key: 'b2', url: 'https://g/b2', time: '2026-07-13T09:20:00' }),
          ])
        ]}
        window={{ start: '2026-07-13T09:00:00', end: '2026-07-13T09:20:00' }}
      />
    );
    expect(container.querySelector('.tlNote')).toBeNull();
    expect(container.querySelector('.tlGapLabel')).toBeNull();
  });

  it('hands the working hours the width an empty window used to take', () => {
    const { container } = render(
      <Timeline
        members={[
          member([
            evidence({ time: '2026-07-13T09:00:00' }),
            evidence({ key: 'b2', url: 'https://g/b2', time: '2026-07-13T09:30:00' }),
          ]),
        ]}
        window={WINDOW}
      />
    );
    const lefts = [...marks(container)].map((node) => parseFloat((node as HTMLElement).style.left));
    // Drawn to scale inside a midnight-to-18:00 window these sat 2% apart.
    expect(Math.abs((lefts[1] as number) - (lefts[0] as number))).toBeGreaterThan(50);
  });

  it('carries the date on the first tick of each day once the board spans days', () => {
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
    const dayTicks = [...container.querySelectorAll('.tlTickDay')].map((t) => t.textContent);
    expect(dayTicks.length).toBeGreaterThan(0);
    expect(dayTicks[0]).toMatch(/^(Sun|Mon|Tue|Wed|Thu|Fri|Sat) \d{2}-\d{2} \d{2}:\d{2}$/);
  });

  it('derives the axis from the events when the window is empty', () => {
    const { container } = render(<Timeline members={[member([evidence()])]} window={{ start: '', end: '' }} />);
    expect(container.querySelectorAll('.tlTick').length).toBeGreaterThan(0);
    const left = parseFloat((container.querySelector('.tlMinor') as HTMLElement).style.left);
    expect(left).toBeGreaterThan(40);
    expect(left).toBeLessThan(60);
  });

  it('plots events that fall outside the window instead of clipping them', () => {
    const { container } = render(
      <Timeline members={[member([evidence({ time: '2026-07-12T09:00:00' })])]} window={WINDOW} />
    );
    const left = parseFloat((container.querySelector('.tlMinor') as HTMLElement).style.left);
    expect(left).toBeGreaterThanOrEqual(0);
    expect(left).toBeLessThanOrEqual(100);
  });

  /* ---- the two tiers --------------------------------------------------- */

  it('gives landmarks a disc and minor work a stroke', () => {
    const { container } = render(
      <Timeline
        members={[
          member([
            evidence({ time: '2026-07-13T09:00:00' }),
            evidence({ kind: 'pr', key: '#91', url: 'https://g/pr/91', time: '2026-07-13T15:00:00' }),
          ]),
        ]}
        window={WINDOW}
      />
    );
    expect(container.querySelectorAll('.tlMinor')).toHaveLength(1);
    expect(container.querySelectorAll('.tlDot')).toHaveLength(1);
    // Only the disc carries a glyph; a stroke has no room for one.
    expect(container.querySelectorAll('.tlDot svg')).toHaveLength(1);
  });

  it('merges near-simultaneous landmarks into one disc with a count badge', () => {
    const twoClose = [
      evidence({ kind: 'issue', key: 'YB-1', url: 'https://j/1', time: '2026-07-13T09:15:00' }),
      evidence({ kind: 'issue', key: 'YB-2', url: 'https://j/2', time: '2026-07-13T09:15:04' }),
    ];
    const { container, rerender } = render(<Timeline members={[member(twoClose)]} window={WINDOW} />);
    expect(container.querySelectorAll('.tlDot')).toHaveLength(1);
    expect(container.querySelector('.tlBadge')?.textContent).toBe('×2');

    const twoFar = [
      evidence({ kind: 'issue', key: 'YB-1', url: 'https://j/1', time: '2026-07-13T09:15:00' }),
      evidence({ kind: 'issue', key: 'YB-2', url: 'https://j/2', time: '2026-07-13T15:15:00' }),
    ];
    rerender(<Timeline members={[member(twoFar)]} window={WINDOW} />);
    expect(container.querySelectorAll('.tlDot')).toHaveLength(2);
    expect(container.querySelector('.tlBadge')).toBeNull();
  });

  it('hoists PR child commits into their own marks', () => {
    const pr = evidence({
      kind: 'pr',
      key: '#91',
      url: 'https://g/pr/91',
      time: '2026-07-13T12:00:00',
      children: [evidence({ key: 'aaa1', url: 'https://g/aaa1', time: '2026-07-13T08:00:00' })],
    });
    const { container } = render(<Timeline members={[member([pr])]} window={WINDOW} />);
    expect(marks(container)).toHaveLength(2);
  });

  it('renders an unknown kind without throwing, as minor work', () => {
    const { container } = render(
      <Timeline members={[member([evidence({ kind: 'weird-new-kind' })])]} window={WINDOW} />
    );
    const mark = container.querySelector('.tlMinor');
    expect(mark).not.toBeNull();
    expect(mark?.getAttribute('aria-label')).toContain('weird-new-kind');
  });

  /* ---- threads --------------------------------------------------------- */

  it('threads the lanes that touched the same pull request, and names them in words', () => {
    const pr = 'https://github.com/acme/web/pull/91';
    const { container } = render(
      <Timeline
        members={[
          member(
            [evidence({ kind: 'pr', key: '#91', url: pr, status: 'merged', time: '2026-07-13T14:00:00' })],
            'Ada Lovelace'
          ),
          member(
            [
              evidence({
                kind: 'review',
                key: 'review:12345',
                title: 'Reviewed PR #91: Fix login redirect',
                url: `${pr}#pullrequestreview-12345`,
                status: 'approved',
                time: '2026-07-13T11:00:00',
              }),
            ],
            'Grace Hopper'
          ),
        ]}
        window={WINDOW}
      />
    );
    expect(container.querySelectorAll('.tlThread')).toHaveLength(1);
    // The line is aria-hidden, so the relationship must also be said in words.
    const labels = [...marks(container)].map((node) => node.getAttribute('aria-label'));
    expect(labels.some((label) => label?.includes('Also touched by Grace Hopper'))).toBe(true);
    expect(labels.some((label) => label?.includes('Also touched by Ada Lovelace'))).toBe(true);
  });

  it('draws no thread when nobody shares an artifact', () => {
    const { container } = render(
      <Timeline
        members={[
          member([evidence({ kind: 'pr', key: '#91', url: 'https://github.com/acme/web/pull/91' })], 'Ada'),
          member([evidence({ kind: 'pr', key: '#92', url: 'https://github.com/acme/web/pull/92' })], 'Grace'),
        ]}
        window={WINDOW}
      />
    );
    expect(container.querySelectorAll('.tlThread')).toHaveLength(0);
    expect([...marks(container)].every((n) => !n.getAttribute('aria-label')?.includes('Also touched'))).toBe(true);
  });

  /* ---- contracts and hostile data -------------------------------------- */

  it('links every mark to the member card anchor the jump strip uses', () => {
    const { container } = render(<Timeline members={[member([evidence()])]} window={WINDOW} />);
    expect(container.querySelector('.tlMinor')?.getAttribute('href')).toBe(`#m-${memberSlug('Ada Lovelace')}`);
    expect(memberSlug('Ada Lovelace')).toBe('ada-lovelace');
  });

  it('excludes undated rows from the plot rather than guessing a position', () => {
    const { container } = render(
      <Timeline
        members={[member([evidence(), evidence({ kind: 'wip', key: 'YB-9', url: 'https://j/YB-9', time: '' })])]}
        window={WINDOW}
      />
    );
    expect(marks(container)).toHaveLength(1);
    expect(container.querySelector('.tlUndated')?.textContent).toBe('+1 undated');
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
    const commitKey = [...container.querySelectorAll('.tlLegendItem')].find((b) => b.textContent === 'commit');
    fireEvent.click(commitKey as Element);
    expect(commitKey?.getAttribute('aria-pressed')).toBe('true');
    expect(container.querySelectorAll('.tlSlot.tlDimmed')).toHaveLength(1);
    fireEvent.click(commitKey as Element);
    expect(container.querySelectorAll('.tlSlot.tlDimmed')).toHaveLength(0);
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
        members={[member([evidence({ kind: 'page', key: '1892385692', title: 'MFA Runbook', url: 'https://c/p' })])]}
        window={WINDOW}
      />
    );
    expect(container.textContent).not.toContain('1892385692');
    expect(container.querySelector('.tlDot')?.getAttribute('aria-label')).toContain('MFA Runbook');
    expect([...container.querySelectorAll('.tlCap')].map((n) => n.textContent)).toEqual(['MFA Runbook']);
  });

  it('has no axe violations on a populated board', async () => {
    const pr = 'https://github.com/acme/web/pull/91';
    const { container } = render(
      <Timeline
        members={[
          member(
            [
              evidence({ time: '2026-07-13T09:00:00' }),
              evidence({ kind: 'pr', key: '#91', url: pr, status: 'merged', time: '2026-07-13T15:00:00' }),
              evidence({ kind: 'wip', key: 'YB-9', url: 'https://j/YB-9', time: '' }),
            ],
            'Ada Lovelace'
          ),
          member(
            [evidence({ kind: 'review', key: 'review:1', url: `${pr}#pullrequestreview-1`, status: 'approved' })],
            'Grace Hopper'
          ),
        ]}
        window={WINDOW}
      />
    );
    expect(await axe(container)).toHaveNoViolations();
  });
});
