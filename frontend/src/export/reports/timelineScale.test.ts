/**
 * The timeline's geometry.
 *
 * Worth pinning: the invariants a compressed axis must never break (monotonic,
 * in range, order-preserving), that idle stretches actually surrender their
 * width *and* stay labelled with what they stood for, the degenerate inputs
 * that would otherwise produce NaN positions, and the URL shapes each source
 * really emits — `artifactId` is the single point of failure for the threads,
 * and a wrong regex there fails silently by drawing nothing.
 */

import { describe, expect, it } from 'vitest';

// The stylesheet's own text, so the geometry guard below compares the real
// declarations rather than a copy of them.
import cssText from './timeline.module.css?raw';

import {
  artifactId,
  BAND_H_REM,
  buildScale,
  buildThreads,
  CAPTION_MIN_PCT,
  fmtDuration,
  MAX_THREADS,
  parseTime,
  refToken,
  TRACK_CENTRE,
  TRACK_H_REM,
} from './timelineScale';

const T = (iso: string) => parseTime(iso);
const DAY = '2026-07-13';
const at = (clock: string) => T(`${DAY}T${clock}:00`);

describe('buildScale', () => {
  it('keeps positions monotonic and inside the track', () => {
    const times = ['08:15', '08:20', '09:00', '13:40', '13:41', '17:55'].map(at);
    const scale = buildScale(times, { start: `${DAY}T00:00:00`, end: `${DAY}T23:59:00` });
    const positions = times.map((t) => scale.pct(t));
    for (const p of positions) {
      expect(p).toBeGreaterThanOrEqual(0);
      expect(p).toBeLessThanOrEqual(100);
    }
    for (let i = 1; i < positions.length; i += 1) {
      expect(positions[i] as number).toBeGreaterThanOrEqual(positions[i - 1] as number);
    }
    // Segment widths tile the track exactly.
    const total = scale.segments.reduce((sum, seg) => sum + seg.width, 0);
    expect(total).toBeCloseTo(100, 6);
  });

  it('compresses an idle stretch into a notch that states its own duration', () => {
    const scale = buildScale([at('09:00'), at('15:00')]);
    expect(scale.compressed).toBe(true);
    expect(scale.gaps).toHaveLength(1);
    // 09:10 → 14:50 once both footprints are taken out.
    expect(scale.gaps[0]?.label).toBe('5h 40m');
    expect(scale.gaps[0]?.width).toBeLessThan(4);
  });

  it('gives the working hours the width the empty window used to take', () => {
    // The complaint case: work 09:00–09:30 inside a midnight-to-18:00 window.
    const scale = buildScale([at('09:00'), at('09:30')], {
      start: `${DAY}T00:00:00`,
      end: `${DAY}T18:00:00`,
    });
    expect(scale.gaps).toHaveLength(2);
    const spread = scale.pct(at('09:30')) - scale.pct(at('09:00'));
    // Drawn to scale this pair sat 2% apart; the notches hand the width back.
    expect(spread).toBeGreaterThan(50);
  });

  it('stays linear and uncompressed when nothing is idle', () => {
    const times = ['09:00', '09:20', '09:40'].map(at);
    const scale = buildScale(times);
    expect(scale.compressed).toBe(false);
    expect(scale.gaps).toHaveLength(0);
    expect(scale.segments).toHaveLength(1);
    // One footprint each side of a 40-minute span, so the pair sits inside.
    expect(scale.pct(times[1] as number)).toBeCloseTo(50, 4);
  });

  it('centres a lone event instead of dividing by zero', () => {
    const scale = buildScale([at('11:00')]);
    const p = scale.pct(at('11:00'));
    expect(p).toBeGreaterThan(40);
    expect(p).toBeLessThan(60);
  });

  it('survives every event landing on the same instant', () => {
    const scale = buildScale([at('11:00'), at('11:00'), at('11:00')]);
    expect(scale.segments.every((seg) => Number.isFinite(seg.width))).toBe(true);
    expect(scale.pct(at('11:00'))).toBeGreaterThan(0);
    expect(scale.pct(at('11:00'))).toBeLessThan(100);
  });

  it('returns a drawable scale for no events at all', () => {
    const scale = buildScale([]);
    expect(scale.segments).toHaveLength(1);
    expect(scale.pct(0)).toBe(0);
    expect(scale.gaps).toHaveLength(0);
  });

  it('caps the width all the notches take together', () => {
    // One event every three hours for four days: many idle stretches.
    const times: number[] = [];
    for (let i = 0; i < 32; i += 1) times.push(T('2026-07-10T00:00:00') + i * 3 * 3_600_000);
    const scale = buildScale(times);
    const quiet = scale.gaps.reduce((sum, gap) => sum + gap.width, 0);
    expect(scale.gaps.length).toBeGreaterThan(20);
    expect(quiet).toBeLessThanOrEqual(26.001);
  });

  it('absorbs a window edge too short to be worth a notch', () => {
    // Window opens 15 minutes before the first footprint — under QUIET_MIN.
    const scale = buildScale([at('09:00')], { start: `${DAY}T08:35:00`, end: `${DAY}T09:10:00` });
    expect(scale.gaps).toHaveLength(0);
    expect(scale.segments).toHaveLength(1);
  });

  it('labels each active segment with its real events, not its footprint edges', () => {
    const scale = buildScale([at('09:00'), at('09:50'), at('16:00')]);
    // Never "08:50" — that edge is an internal footprint boundary, and naming it
    // on the axis claims the day started ten minutes before anything happened.
    expect(scale.ticks.map((tick) => tick.label)).toEqual(['09:00', '09:50', '16:00']);
    expect(scale.ticks.every((tick) => tick.left >= 0 && tick.left <= 100)).toBe(true);
  });

  it('carries the date on the first tick of each day once the board spans days', () => {
    const scale = buildScale([T('2026-07-10T09:00:00'), T('2026-07-13T15:00:00')]);
    expect(scale.multiDay).toBe(true);
    const dayTicks = scale.ticks.filter((tick) => tick.day);
    expect(dayTicks).toHaveLength(2);
    expect(dayTicks[0]?.label).toMatch(/^(Sun|Mon|Tue|Wed|Thu|Fri|Sat) \d{2}-\d{2} \d{2}:\d{2}$/);
  });

  it('clamps a timestamp outside the domain instead of running off the track', () => {
    const scale = buildScale([at('09:00')]);
    expect(scale.pct(at('09:00') - 99 * 3_600_000)).toBe(0);
    expect(scale.pct(at('09:00') + 99 * 3_600_000)).toBe(100);
    expect(scale.pct(NaN)).toBe(0);
  });

  it('reads a naive stamp and an AzDO space-separated one alike', () => {
    expect(parseTime('2026-07-13 09:15:00')).toBe(parseTime('2026-07-13T09:15:00'));
    expect(Number.isNaN(parseTime(''))).toBe(true);
    expect(Number.isNaN(parseTime('not a date'))).toBe(true);
  });
});

describe('fmtDuration', () => {
  it('names a stretch in the coarsest unit that stays honest', () => {
    expect(fmtDuration(45 * 60_000)).toBe('45m');
    expect(fmtDuration(3 * 3_600_000)).toBe('3h');
    expect(fmtDuration(3 * 3_600_000 + 20 * 60_000)).toBe('3h 20m');
    expect(fmtDuration(26 * 3_600_000)).toBe('1d 2h');
    expect(fmtDuration(48 * 3_600_000)).toBe('2d');
    // Never "0m" — a notch always stood for something.
    expect(fmtDuration(20)).toBe('1m');
  });
});

describe('artifactId', () => {
  it('reads the pull request out of the shapes each source really emits', () => {
    // GitHub PR row.
    expect(artifactId('https://github.com/acme/web/pull/91', '#91')).toBe('https://github.com/acme/web/pull/91');
    // GitHub review row — the key is the *review* id, so only the URL helps.
    expect(artifactId('https://github.com/acme/web/pull/91#pullrequestreview-12345', 'review:12345')).toBe(
      'https://github.com/acme/web/pull/91'
    );
    // GitHub issue comment on a PR.
    expect(artifactId('https://github.com/acme/web/pull/91#issuecomment-777', 'comment:777')).toBe(
      'https://github.com/acme/web/pull/91'
    );
    // Azure DevOps PR row, and a thread row on the same PR.
    expect(artifactId('https://dev.azure.com/org/Proj/_git/web/pullrequest/91', '!91')).toBe(
      'https://dev.azure.com/org/proj/_git/web/pullrequest/91'
    );
    expect(artifactId('https://dev.azure.com/org/Proj/_git/web/pullrequest/91?discussionId=7', 'review:91:x')).toBe(
      'https://dev.azure.com/org/proj/_git/web/pullrequest/91'
    );
    // A deeper path still resolves to the pull request itself.
    expect(artifactId('https://github.com/acme/web/pull/91/files', '')).toBe('https://github.com/acme/web/pull/91');
  });

  it('falls back to a ticket key, and refuses anything else', () => {
    expect(artifactId('', 'YB-12')).toBe('ticket:YB-12');
    expect(artifactId('https://acme.atlassian.net/browse/YB-12', 'YB-12')).toBe('ticket:YB-12');
    expect(artifactId('', 'yb-12')).toBe('ticket:YB-12');
    expect(artifactId('', '78e4201d')).toBe('');
    expect(artifactId('https://example.invalid/c/aaa1', 'aaa1')).toBe('');
    expect(artifactId('', '')).toBe('');
  });
});

describe('refToken', () => {
  it('reuses the sigil the producer wrote', () => {
    expect(refToken('approved PR !91: Fix login redirect (web)')).toBe('!91');
    expect(refToken('Reviewed PR #91: Enable SSO')).toBe('#91');
    expect(refToken('no reference here')).toBe('');
  });
});

describe('buildThreads', () => {
  const pr = 'https://github.com/acme/web/pull/91';

  it('links the lanes that touched one artifact, in time order', () => {
    const { threads, partners } = buildThreads([
      { lane: 0, at: at('09:00'), artifact: pr },
      { lane: 1, at: at('11:00'), artifact: pr },
      { lane: 0, at: at('14:00'), artifact: pr },
    ]);
    expect(threads).toHaveLength(2);
    expect(threads[0]).toMatchObject({ fromLane: 0, toLane: 1 });
    expect(threads[1]).toMatchObject({ fromLane: 1, toLane: 0 });
    expect(partners.get(pr)).toEqual([0, 1]);
  });

  it('steps over a same-lane touch rather than ending the chain', () => {
    const { threads } = buildThreads([
      { lane: 0, at: at('09:00'), artifact: pr },
      { lane: 0, at: at('10:00'), artifact: pr },
      { lane: 1, at: at('11:00'), artifact: pr },
    ]);
    expect(threads).toHaveLength(1);
    expect(threads[0]).toMatchObject({ fromLane: 0, toLane: 1, fromAt: at('10:00') });
  });

  it('draws nothing when only one person touched it', () => {
    const { threads, partners } = buildThreads([
      { lane: 0, at: at('09:00'), artifact: pr },
      { lane: 0, at: at('14:00'), artifact: pr },
    ]);
    expect(threads).toHaveLength(0);
    expect(partners.size).toBe(0);
  });

  it('ignores events that name no artifact', () => {
    const { threads } = buildThreads([
      { lane: 0, at: at('09:00'), artifact: '' },
      { lane: 1, at: at('11:00'), artifact: '' },
    ]);
    expect(threads).toHaveLength(0);
  });

  it('caps the threads and reports what it dropped', () => {
    const events = [];
    for (let i = 0; i < MAX_THREADS + 6; i += 1) {
      events.push({ lane: 0, at: at('09:00') + i, artifact: `${pr}${i}` });
      events.push({ lane: 1, at: at('09:00') + i + 1, artifact: `${pr}${i}` });
    }
    const { threads, dropped } = buildThreads(events);
    expect(threads).toHaveLength(MAX_THREADS);
    expect(dropped).toBe(6);
  });
});

describe('caption geometry', () => {
  it('caps a caption at no more than the spacing that separates them', () => {
    // The two numbers live in different files and must be in the same unit. A
    // `ch` cap here read as clear at 1440px and overlapped at 700px, because a
    // fixed character count is a growing share of a shrinking track.
    const declared = /--tl-cap-w:\s*([\d.]+)%/.exec(cssText);
    expect(declared).not.toBeNull();
    const width = parseFloat((declared as RegExpExecArray)[1] as string);
    // Captions are centred, so two adjacent ones each claim half their width.
    expect(width).toBeLessThanOrEqual(CAPTION_MIN_PCT);
  });
});

describe('track geometry', () => {
  it('agrees with the CSS the thread overlay is positioned against', () => {
    // The overlay derives each lane's track centre from these numbers; if the
    // stylesheet and the module disagree, every thread shears off its dots.
    expect(cssText).toContain(`--tl-track-h: ${TRACK_H_REM}rem`);
    expect(cssText).toContain(`--tl-band-h: ${BAND_H_REM}rem`);
    expect(TRACK_CENTRE).toBeCloseTo(TRACK_H_REM / 2 / (TRACK_H_REM + BAND_H_REM), 10);
  });
});
