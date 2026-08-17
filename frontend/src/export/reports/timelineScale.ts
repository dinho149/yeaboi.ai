/**
 * The activity timeline's geometry — a piecewise time scale, and the links
 * between the people who touched the same artifact.
 *
 * Pure functions, no DOM: this is where the correctness of the picture lives,
 * so it is testable without rendering anything.
 *
 * **Why not a linear axis.** A standup window is mostly empty. Drawn true to
 * scale, a 24-hour window spends half its width on the night and squeezes the
 * hours anyone worked into the remainder. So each event claims a small
 * *footprint* around itself, overlapping footprints merge into **active**
 * segments that get width in proportion to their real duration, and the dead
 * stretches between them become fixed-width **quiet** segments — notches.
 *
 * A compressed axis is no longer linear, which on a time chart is a lie unless
 * the page says so. Every notch carries its own elapsed duration (`Gap.label`)
 * and the component prints a caption whenever `Scale.compressed` is true. Any
 * change here that hides a compression breaks that contract.
 */

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/** How much axis an event claims on each side of itself. */
export const EVENT_HALF_WIDTH = 10 * MINUTE;
/** A stretch between footprints longer than this compresses to a notch. */
export const QUIET_MIN = 40 * MINUTE;
/** Track share one notch would like… */
const QUIET_WIDTH = 3.2;
/** …and the ceiling on all of them together, so the work keeps the width. */
const QUIET_TOTAL_MAX = 26;
/** Above this domain span, labels carry the day as well as the clock. */
export const DAY_AXIS_SPAN = 30 * HOUR;
/** An active segment at least this wide (%) labels its right edge too. */
const TICK_BOTH_ENDS = 18;

/**
 * Track geometry, mirrored by `--tl-track-h` / `--tl-band-h` in
 * timeline.module.css. The thread overlay derives each lane's track centre
 * from these, so the two must agree — `timelineScale.test.ts` parses the CSS
 * and fails when they drift.
 */
export const TRACK_H_REM = 2.2;
export const BAND_H_REM = 1.4;
/** Where a lane's track centre sits within its row, as a fraction. */
export const TRACK_CENTRE = TRACK_H_REM / 2 / (TRACK_H_REM + BAND_H_REM);

/**
 * How far apart two captions must stand, as a share of the track.
 *
 * Nothing is ever measured, so this is the *only* thing preventing captions
 * from colliding — which means it has to be denominated in the same unit as the
 * caption's own width. `--tl-cap-w` in timeline.module.css caps a caption at a
 * percentage of the track for exactly that reason (a `ch` cap is absolute, so it
 * stayed clear at 1440px and overlapped at 700px). Keep
 * `--tl-cap-w <= CAPTION_MIN_PCT`; `timelineScale.test.ts` enforces it.
 */
export const CAPTION_MIN_PCT = 13;

export interface Segment {
  from: number;
  to: number;
  quiet: boolean;
  /** Left edge as a percentage of the track. */
  left: number;
  /** Width as a percentage of the track. */
  width: number;
}

export interface Gap {
  left: number;
  width: number;
  /** Real elapsed time the notch stands in for. */
  minutes: number;
  /** Human duration, e.g. `3h 20m`. */
  label: string;
}

export interface Tick {
  at: number;
  label: string;
  /** Starts a new local day — the label carries the date. */
  day: boolean;
  left: number;
}

export interface Scale {
  segments: Segment[];
  gaps: Gap[];
  ticks: Tick[];
  /** Position a timestamp on the track, clamped to `[0, 100]`. */
  pct(at: number): number;
  /**
   * Which segment a timestamp falls in, `-1` outside the domain. Callers use
   * it to group events by *burst* — one active segment is one burst, which is
   * the only grouping that survives a compressed axis (a distance in percent
   * means different things either side of a notch).
   */
  segmentIndex(at: number): number;
  /** True when at least one stretch was compressed — the page must say so. */
  compressed: boolean;
  /** True when the domain spans more than a day; labels carry the date. */
  multiDay: boolean;
  from: number;
  to: number;
}

/** Naive stamps read as viewer-local; AzDO's space separator is normalised. */
export function parseTime(value: string): number {
  if (!value) return NaN;
  return Date.parse(value.replace(' ', 'T'));
}

function two(n: number): string {
  return String(n).padStart(2, '0');
}

export function fmtClock(at: number): string {
  const d = new Date(at);
  return `${two(d.getHours())}:${two(d.getMinutes())}`;
}

const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'] as const;

export function fmtDay(at: number): string {
  const d = new Date(at);
  return `${WEEKDAYS[d.getDay()]} ${two(d.getMonth() + 1)}-${two(d.getDate())}`;
}

export function fmtWhen(at: number, multiDay: boolean): string {
  return multiDay ? `${fmtDay(at)} ${fmtClock(at)}` : fmtClock(at);
}

/** A quiet stretch's own duration, in the coarsest unit that stays honest. */
export function fmtDuration(ms: number): string {
  const minutes = Math.max(1, Math.round(ms / MINUTE));
  if (minutes < 60) return `${minutes}m`;
  if (ms < DAY) {
    const hours = Math.floor(minutes / 60);
    const rest = minutes % 60;
    return rest ? `${hours}h ${rest}m` : `${hours}h`;
  }
  const days = Math.floor(ms / DAY);
  const hours = Math.round((ms - days * DAY) / HOUR);
  return hours ? `${days}d ${hours}h` : `${days}d`;
}

interface Run {
  from: number;
  to: number;
  /** The first and last real event in the run — what the axis labels name. */
  firstAt: number;
  lastAt: number;
}

/** Footprints around each event, merged where they touch. */
function activeRuns(times: number[]): Run[] {
  const runs: Run[] = [];
  for (const at of times) {
    const from = at - EVENT_HALF_WIDTH;
    const to = at + EVENT_HALF_WIDTH;
    const last = runs[runs.length - 1];
    // Merge when the stretch between the two footprints is not worth a notch,
    // so a burst of commits reads as one run rather than a row of notches.
    if (last && from - last.to <= QUIET_MIN) {
      last.to = Math.max(last.to, to);
      last.lastAt = at;
    } else {
      runs.push({ from, to, firstAt: at, lastAt: at });
    }
  }
  return runs;
}

/**
 * Build the scale from every dated event on the board, plus the reported
 * window. The window only ever *extends* the domain: its empty leading and
 * trailing hours become notches rather than dead width, so "nothing since
 * 16:45" stays visible without costing a third of the track.
 */
export function buildScale(times: number[], bounds?: { start: string; end: string }): Scale {
  const sorted = [...new Set(times.filter((at) => Number.isFinite(at)))].sort((a, b) => a - b);
  const runs = activeRuns(sorted);
  const first = runs[0];
  const last = runs[runs.length - 1];
  // With no dated events there is nothing to draw; callers return null before
  // this, but a degenerate scale beats a NaN one.
  let from = first ? first.from : 0;
  let to = last ? last.to : 1;

  const windowStart = parseTime(bounds?.start ?? '');
  const windowEnd = parseTime(bounds?.end ?? '');
  if (Number.isFinite(windowStart)) from = Math.min(from, windowStart);
  if (Number.isFinite(windowEnd)) to = Math.max(to, windowEnd);

  // A window edge too short to be worth a notch is absorbed into the run it
  // touches, so the axis never carries two adjacent active segments (which
  // would print two labels for one stretch of work).
  if (first && last) {
    if (first.from - from <= QUIET_MIN) first.from = from;
    if (to - last.to <= QUIET_MIN) last.to = to;
  }

  // Interleave the runs with the stretches between them. Every remaining
  // stretch is longer than QUIET_MIN by construction — `activeRuns` already
  // merged the shorter ones — so each is a notch.
  const raw: { from: number; to: number; quiet: boolean; firstAt?: number; lastAt?: number }[] = [];
  if (!runs.length) {
    raw.push({ from, to, quiet: false });
  } else {
    if ((first as Run).from > from) {
      raw.push({ from, to: (first as Run).from, quiet: true });
    }
    runs.forEach((run, index) => {
      raw.push({ from: run.from, to: run.to, quiet: false, firstAt: run.firstAt, lastAt: run.lastAt });
      const next = runs[index + 1];
      if (next) raw.push({ from: run.to, to: next.from, quiet: true });
    });
    if (to > (last as Run).to) {
      raw.push({ from: (last as Run).to, to, quiet: true });
    }
  }

  const quietCount = raw.filter((seg) => seg.quiet).length;
  const quietWidth = quietCount ? Math.min(QUIET_WIDTH, QUIET_TOTAL_MAX / quietCount) : 0;
  const activeSegments = raw.filter((seg) => !seg.quiet);
  const activeTotal = activeSegments.reduce((sum, seg) => sum + (seg.to - seg.from), 0);
  const activeShare = 100 - quietWidth * quietCount;

  const segments: (Segment & { firstAt?: number; lastAt?: number })[] = [];
  let cursor = 0;
  for (const seg of raw) {
    let width: number;
    if (seg.quiet) {
      width = quietWidth;
    } else if (activeTotal > 0) {
      width = ((seg.to - seg.from) / activeTotal) * activeShare;
    } else {
      // Every event at the same instant: share the width evenly.
      width = activeShare / Math.max(1, activeSegments.length);
    }
    segments.push({
      from: seg.from,
      to: seg.to,
      quiet: seg.quiet,
      left: cursor,
      width,
      ...(seg.firstAt === undefined ? {} : { firstAt: seg.firstAt, lastAt: seg.lastAt }),
    });
    cursor += width;
  }

  const pct = (at: number): number => {
    if (!Number.isFinite(at)) return 0;
    if (at <= from) return 0;
    if (at >= to) return 100;
    for (const seg of segments) {
      if (at >= seg.from && at <= seg.to) {
        const span = seg.to - seg.from;
        return seg.left + (span > 0 ? ((at - seg.from) / span) * seg.width : seg.width / 2);
      }
    }
    return 100;
  };

  const segmentIndex = (at: number): number =>
    segments.findIndex((seg) => at >= seg.from && at <= seg.to);

  const gaps: Gap[] = segments
    .filter((seg) => seg.quiet)
    .map((seg) => ({
      left: seg.left,
      width: seg.width,
      minutes: Math.round((seg.to - seg.from) / MINUTE),
      label: fmtDuration(seg.to - seg.from),
    }));

  const multiDay = to - from > DAY_AXIS_SPAN;

  // One label per active segment rather than a regular grid: on a compressed
  // axis a fixed step would land inside notches and lie about the spacing.
  //
  // The label names the segment's first *real* event, not its edge. The edge is
  // a footprint boundary — an internal device — and labelling the axis "08:57"
  // when the day began at 09:07 invites exactly the wrong question.
  const ticks: Tick[] = [];
  let previousDay = '';
  for (const seg of segments) {
    if (seg.quiet) continue;
    const opens = seg.firstAt ?? seg.from;
    const closes = seg.lastAt ?? seg.to;
    const startDay = fmtDay(opens);
    const newDay = multiDay && startDay !== previousDay;
    previousDay = startDay;
    ticks.push({
      at: opens,
      label: newDay ? `${startDay} ${fmtClock(opens)}` : fmtClock(opens),
      day: newDay,
      left: pct(opens),
    });
    if (seg.width >= TICK_BOTH_ENDS && closes > opens) {
      ticks.push({ at: closes, label: fmtClock(closes), day: false, left: pct(closes) });
    }
  }

  return { segments, gaps, ticks, pct, segmentIndex, compressed: quietCount > 0, multiDay, from, to };
}

/* ---- who touched the same thing ---------------------------------------- */

/**
 * The artifact an event refers to, or `""` when it names nothing shareable.
 *
 * The URL is the only reliable source. GitHub's review rows key on the
 * *review* id (`review:123`) and AzDO's comment rows on the comment id
 * (`review-comment-7`) — neither carries the pull-request number — while both
 * sources put it in the path (`/pull/91`, `/pullrequest/91`, the latter with a
 * `?discussionId=` tail). Ticket keys are the one non-URL case worth matching.
 */
const PR_PATH = /^(.*\/(?:pull|pullrequest)\/\d+)(?:\/|$)/;
const TICKET_KEY = /^[A-Za-z][A-Za-z0-9]*-\d+$/;

export function artifactId(url: string, key: string): string {
  const bare = (url || '').split(/[?#]/)[0] ?? '';
  const path = PR_PATH.exec(bare);
  if (path) return (path[1] as string).toLowerCase();
  const trimmed = (key || '').trim();
  if (TICKET_KEY.test(trimmed)) return `ticket:${trimmed.toUpperCase()}`;
  return '';
}

/** The pull-request token a producer used in its own prose, e.g. `#91`, `!91`. */
export function refToken(title: string): string {
  return /(?:^|\s)([#!]\d+)\b/.exec(title || '')?.[1] ?? '';
}

/** A cap on drawn threads: a hairball says less than no threads at all. */
export const MAX_THREADS = 24;

export interface Thread {
  fromLane: number;
  toLane: number;
  fromAt: number;
  toAt: number;
  artifact: string;
}

export interface ThreadInput {
  lane: number;
  at: number;
  artifact: string;
}

/**
 * Threads between lanes that touched the same artifact, in time order.
 *
 * Consecutive same-lane touches are stepped over rather than ending the chain,
 * so "Ada opened → Ada pushed → Grace approved → Ada merged" still links Ada
 * to Grace twice instead of once. `partners` names the other people on each
 * artifact, which is what lets a dot say the relationship in words — a drawn
 * line alone would be shape-only information.
 */
export function buildThreads(events: ThreadInput[]): {
  threads: Thread[];
  dropped: number;
  partners: Map<string, number[]>;
} {
  const byArtifact = new Map<string, ThreadInput[]>();
  for (const event of events) {
    if (!event.artifact) continue;
    const bucket = byArtifact.get(event.artifact);
    if (bucket) bucket.push(event);
    else byArtifact.set(event.artifact, [event]);
  }

  const threads: Thread[] = [];
  const partners = new Map<string, number[]>();
  // Sorted so a capped board drops the same threads every render.
  for (const artifact of [...byArtifact.keys()].sort()) {
    const touches = (byArtifact.get(artifact) as ThreadInput[]).slice().sort((a, b) => a.at - b.at);
    const lanes = [...new Set(touches.map((touch) => touch.lane))];
    if (lanes.length < 2) continue;
    partners.set(artifact, lanes);
    let previous = touches[0] as ThreadInput;
    for (const touch of touches.slice(1)) {
      if (touch.lane !== previous.lane) {
        threads.push({
          fromLane: previous.lane,
          toLane: touch.lane,
          fromAt: previous.at,
          toAt: touch.at,
          artifact,
        });
      }
      previous = touch;
    }
  }

  return {
    threads: threads.slice(0, MAX_THREADS),
    dropped: Math.max(0, threads.length - MAX_THREADS),
    partners,
  };
}
