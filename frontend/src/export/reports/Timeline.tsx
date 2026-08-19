/**
 * The activity timeline — the standup's opening picture.
 *
 * One compressed time axis across the day, one swimlane per member, every dated
 * evidence row a mark: what each person did, when, in what order, and where two
 * people met on the same pull request.
 *
 * Three things carry the information, in this order of importance:
 *
 * 1. **Captions on the board.** Landmarks (a PR, a review, a ticket move, a doc)
 *    print their own label; a run of commits prints its count. This is what
 *    makes the picture survive a screenshot, a print or a Slack paste — where a
 *    tooltip does not exist, and where the first version of this component said
 *    nothing at all.
 * 2. **Two tiers.** Landmarks are discs with glyphs; commits and comments are
 *    thin strokes at their true positions. The size difference *is* the
 *    hierarchy, so the eye finds the landmarks without reading anything.
 * 3. **The compressed axis** (timelineScale.ts). Idle stretches become notches
 *    labelled with what they stood for, and the working hours take the width
 *    back. Never remove the `.tlNote` caption: an unlabelled compressed axis is
 *    a lie about time.
 *
 * Interactivity is pure client state — exports open over `file://` under a CSP
 * with `connect-src 'none'`, so tooltips are CSS on hover/focus and a mark is a
 * plain `<a href="#m-…">` to the member's card below (the cards already carry
 * those ids for the jump strip). No fetch, ever.
 *
 * Placement rules for awkward data:
 * - `time: ""` rows (carried WIP, some tracker updates) are *excluded* from the
 *   plot — a mark at an invented position would be a lie on a time axis — and
 *   surface as `+N undated` in the rail; the rows stay fully visible in the card
 *   below. Nothing hidden, only folded.
 * - Events outside the reported window (a PR's child commit can predate it)
 *   stretch the domain rather than clip: every mark is plotted.
 * - Timestamps arrive in a mix of naive/offset ISO forms; `Date.parse` reads
 *   naive ones as viewer-local, so cross-source ordering can shift by a tz
 *   delta. Accepted: marks may shift, never crash or vanish. The window bounds
 *   are the exception — they *are* offset-aware (the engine stamps them from an
 *   aware `datetime`), so a viewer in another zone sees the leading and
 *   trailing notches sized by their own offset. Only the notch durations move;
 *   every mark keeps its position relative to the others.
 * - A member whose evidence is entirely undated keeps a rail and an empty
 *   track. They have nothing to plot, but dropping them would quietly shrink
 *   the team the overview appears to describe.
 */

import { Fragment, useState } from 'react';

import { Avatar, Eyebrow } from '../../design/primitives';
import { toneVar } from '../../design/tone';
import { cx } from '../../runtime/cx';
import type { EvidenceItem, StandupMember } from '../boot';
import { KindIcon, kindGroup, kindMeta, type KindGroup } from './KindIcon';
import styles from './timeline.module.css';
import {
  artifactId,
  buildScale,
  buildThreads,
  CAPTION_MIN_PCT,
  fmtClock,
  fmtWhen,
  parseTime,
  refToken,
  TRACK_CENTRE,
  type Scale,
  type ThreadInput,
} from './timelineScale';

/** Anchor-safe member id shared with the card headers, `#m-ada-lovelace`. */
export function memberSlug(name: string): string {
  return (
    name
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '') || 'member'
  );
}

/** The kinds that earn a disc and a caption of their own. */
const LANDMARKS: ReadonlySet<KindGroup> = new Set<KindGroup>(['pr', 'review', 'ticket', 'doc']);
/** Landmarks closer than this share of the track share one disc. */
const CLUSTER_PCT = 1.8;
/** Captions per lane. Beyond this the band is noise, not information. */
const MAX_CAPTIONS = 6;
/** Captions past this rank are hidden on a narrow screen (see the CSS). */
const CAPTION_LOW_RANK = 3;
/** Tooltip rows a cluster lists before folding to "+N more". */
const CLUSTER_TIP_ROWS = 6;
/** Least lateral bulge on a thread, as a share of the track. */
const BEND_MIN = 6;

interface TimelineEvent {
  kind: string;
  key: string;
  title: string;
  repo: string;
  status: string;
  at: number;
  /** The pull request or ticket this row refers to, `""` when none. */
  artifact: string;
}

interface Lane {
  name: string;
  slug: string;
  events: TimelineEvent[];
  undated: number;
  blocked: boolean;
}

/**
 * One member's marks: every category's evidence flattened, a PR's child commits
 * hoisted to their own marks (they are distinct work moments), deduped by the
 * exporter's own rule.
 */
function laneFor(member: StandupMember): Lane {
  const seen = new Set<string>();
  const events: TimelineEvent[] = [];
  let undated = 0;
  const push = (item: EvidenceItem) => {
    const dedupe = item.url || `${item.kind}:${item.key}:${item.title}`;
    if (seen.has(dedupe)) return;
    seen.add(dedupe);
    const at = parseTime(item.time);
    if (Number.isFinite(at)) {
      events.push({
        kind: item.kind,
        key: item.key,
        title: item.title,
        repo: item.repo,
        status: item.status,
        at,
        artifact: artifactId(item.url, item.key),
      });
    } else {
      undated += 1;
    }
  };
  for (const category of member.categories) {
    for (const item of category.evidence) {
      push(item);
      for (const child of item.children ?? []) push(child);
    }
  }
  events.sort((a, b) => a.at - b.at);
  return {
    name: member.name,
    slug: memberSlug(member.name),
    events,
    undated,
    blocked: (member.blockers ?? []).length > 0,
  };
}

/* ---- marks and captions ------------------------------------------------- */

interface Mark {
  landmark: boolean;
  at: number;
  pct: number;
  /** The active segment this mark sits in — its burst. */
  burst: number;
  events: TimelineEvent[];
}

interface Caption {
  pct: number;
  text: string;
  /** A counted run of minor marks rather than a named landmark. */
  counted: boolean;
  rank: number;
}

/**
 * Landmarks cluster (near-simultaneous ones would overlap as discs); minor
 * events do not, because a 2px stroke may overlap harmlessly and keeping its
 * true position is worth more than tidiness.
 */
function marksFor(events: TimelineEvent[], scale: Scale): Mark[] {
  const marks: Mark[] = [];
  let cluster: Mark | null = null;
  for (const event of events) {
    const pct = scale.pct(event.at);
    const burst = scale.segmentIndex(event.at);
    if (!LANDMARKS.has(kindGroup(event.kind))) {
      marks.push({ landmark: false, at: event.at, pct, burst, events: [event] });
      cluster = null;
      continue;
    }
    if (cluster && pct - cluster.pct <= CLUSTER_PCT) {
      cluster.events.push(event);
      continue;
    }
    cluster = { landmark: true, at: event.at, pct, burst, events: [event] };
    marks.push(cluster);
  }
  return marks.sort((a, b) => a.pct - b.pct);
}

/** What a doc row is called: the title is the handle, the machine id never renders. */
function eventLabel(event: TimelineEvent): string {
  const meta = kindMeta(event.kind);
  if (meta.label === 'doc') return event.title || meta.label;
  return event.title || event.key || meta.label;
}

/** The short form that fits on the board. */
function captionFor(event: TimelineEvent): string {
  const group = kindGroup(event.kind);
  if (group === 'doc') return event.title || 'doc';
  if (group === 'pr' || group === 'ticket') {
    return [event.key, event.status].filter(Boolean).join(' ') || eventLabel(event);
  }
  if (group === 'review') {
    // The producer's own sigil for the PR under review, e.g. "approved !91".
    return [event.status || 'review', refToken(event.title)].filter(Boolean).join(' ');
  }
  return event.key || eventLabel(event);
}

/**
 * A burst of minor work. For more than one event the count is the information
 * and the individual shas are not; for a lone one the sha still is.
 */
function runCaption(events: TimelineEvent[]): string {
  if (events.length === 1) return captionFor(events[0] as TimelineEvent);
  const tally = new Map<string, number>();
  for (const event of events) tally.set(event.kind, (tally.get(event.kind) ?? 0) + 1);
  const top = [...tally.entries()].sort((a, b) => b[1] - a[1])[0];
  const label = kindMeta(top ? top[0] : '').label;
  return `${events.length} ${label}s`;
}

/**
 * Which captions to print, decided from positions alone — nothing is measured,
 * so a caption is kept only when it stands `CAPTION_MIN_PCT` clear of every
 * caption already kept. Landmarks are placed first so a run of commits can
 * never crowd out a merged PR; a dropped caption leaves its mark and its
 * tooltip untouched.
 */
function placeCaptions(marks: Mark[]): Caption[] {
  const kept: Caption[] = [];
  const consider = (pct: number, text: string, counted: boolean) => {
    if (!text || kept.length >= MAX_CAPTIONS) return;
    if (kept.some((caption) => Math.abs(caption.pct - pct) < CAPTION_MIN_PCT)) return;
    kept.push({ pct, text, counted, rank: kept.length });
  };

  for (const mark of marks) {
    if (mark.landmark) consider(mark.pct, captionFor(mark.events[0] as TimelineEvent), false);
  }
  // Minor marks sharing a burst read as one run: "3 commits", captioned at its
  // middle. Grouping by burst rather than by distance is what keeps the count
  // honest — on a compressed axis, two marks the same distance apart can be
  // twenty minutes or four hours apart depending on which side of a notch.
  let run: Mark[] = [];
  const flush = () => {
    if (!run.length) return;
    const first = run[0] as Mark;
    const last = run[run.length - 1] as Mark;
    consider((first.pct + last.pct) / 2, runCaption(run.flatMap((mark) => mark.events)), run.length > 1);
    run = [];
  };
  for (const mark of marks) {
    if (mark.landmark) {
      flush();
      continue;
    }
    const previous = run[run.length - 1];
    if (previous && mark.burst !== previous.burst) flush();
    run.push(mark);
  }
  flush();
  return kept;
}

/* ---- legend ------------------------------------------------------------- */

/** Fixed legend order, so the same day draws the same way twice. */
const GROUP_ORDER: readonly KindGroup[] = ['commit', 'pr', 'review', 'comment', 'ticket', 'doc', 'wip', 'ref'];
/** A representative engine kind per group, for the legend's icon and meta. */
const GROUP_KIND: Record<KindGroup, string> = {
  commit: 'commit',
  pr: 'pr',
  review: 'review',
  comment: 'comment',
  ticket: 'ticket',
  doc: 'page',
  wip: 'wip',
  ref: '',
};

/* ---- marks -------------------------------------------------------------- */

function markAria(mark: Mark, name: string, multiDay: boolean, partners: string[]): string {
  const shared = partners.length ? ` Also touched by ${partners.join(', ')}.` : '';
  if (mark.events.length === 1) {
    const event = mark.events[0] as TimelineEvent;
    const meta = kindMeta(event.kind);
    const parts = [fmtWhen(event.at, multiDay), event.status, event.repo].filter(Boolean).join(', ');
    return `${meta.label}: ${eventLabel(event)} — ${parts}.${shared} Jump to ${name}'s update.`;
  }
  const first = mark.events[0] as TimelineEvent;
  const last = mark.events[mark.events.length - 1] as TimelineEvent;
  return (
    `${mark.events.length} events between ${fmtWhen(first.at, multiDay)} and ` +
    `${fmtWhen(last.at, multiDay)}.${shared} Jump to ${name}'s update.`
  );
}

function MarkLink({
  mark,
  name,
  multiDay,
  partners,
}: {
  mark: Mark;
  name: string;
  multiDay: boolean;
  partners: string[];
}) {
  const head = mark.events[0] as TimelineEvent;
  const meta = kindMeta(head.kind);
  const count = mark.events.length;
  // Tooltip alignment is decided from the position — no measurement JS.
  const align = mark.pct < 15 ? styles['tlTipLeft'] : mark.pct > 85 ? styles['tlTipRight'] : undefined;
  const shown = mark.events.slice(0, CLUSTER_TIP_ROWS);
  const folded = count - shown.length;

  return (
    <a
      href={`#m-${memberSlug(name)}`}
      className={cx(mark.landmark ? styles['tlDot'] : styles['tlMinor'], align)}
      style={{ left: `${mark.pct}%`, color: toneVar(meta.tone) }}
      aria-label={markAria(mark, name, multiDay, partners)}
    >
      {mark.landmark ? <KindIcon kind={head.kind} /> : null}
      {count > 1 ? <span className={styles['tlBadge']}>×{count}</span> : null}
      {/* Presentation only — the aria-label above already says all of this. */}
      <span className={styles['tlTip']} aria-hidden="true">
        {count === 1 ? (
          <>
            <strong className={styles['tlTipTitle']}>{eventLabel(head)}</strong>
            {(meta.label !== 'doc' && head.key) || head.repo ? (
              <span className={styles['tlTipMeta']}>
                {[meta.label === 'doc' ? '' : head.key, head.repo].filter(Boolean).join(' · ')}
              </span>
            ) : null}
            <span className={styles['tlTipMeta']}>
              {[fmtWhen(head.at, multiDay), head.status].filter(Boolean).join(' · ')}
            </span>
          </>
        ) : (
          <>
            {shown.map((event, index) => (
              <span key={index} className={styles['tlTipRow']}>
                <i style={{ color: toneVar(kindMeta(event.kind).tone) }}>
                  <KindIcon kind={event.kind} size={10} />
                </i>
                <span className={styles['tlTipRowText']}>{eventLabel(event)}</span>
                <span className={styles['tlTipRowTime']}>{fmtWhen(event.at, multiDay)}</span>
              </span>
            ))}
            {folded > 0 ? <span className={styles['tlTipMeta']}>+{folded} more</span> : null}
          </>
        )}
        {partners.length ? <span className={styles['tlTipMeta']}>with {partners.join(', ')}</span> : null}
      </span>
    </a>
  );
}

/* ---- rail --------------------------------------------------------------- */

/** The day's shape in words and numbers, so the rail is useful on its own. */
function Rail({ lane, multiDay }: { lane: Lane; multiDay: boolean }) {
  const first = lane.events[0] as TimelineEvent;
  const last = lane.events[lane.events.length - 1] as TimelineEvent;
  const tally = new Map<KindGroup, number>();
  for (const event of lane.events) {
    const group = kindGroup(event.kind);
    tally.set(group, (tally.get(group) ?? 0) + 1);
  }
  const span = !first
    ? 'no times recorded'
    : first.at === (last as TimelineEvent).at
      ? fmtWhen(first.at, multiDay)
      : `${fmtWhen(first.at, multiDay)} → ${multiDay ? fmtWhen((last as TimelineEvent).at, true) : fmtClock((last as TimelineEvent).at)}`;

  return (
    <span className={styles['tlRail']}>
      <span className={styles['tlWho']}>
        <Avatar name={lane.name} size={18} />
        <span className={styles['tlWord']}>{lane.name}</span>
        {lane.blocked ? (
          <span className={styles['tlFlag']} title="Flagged an impediment">
            ⚑
          </span>
        ) : null}
      </span>
      <span className={styles['tlSpan']}>
        {lane.events.length ? `${span} · ${lane.events.length} event${lane.events.length === 1 ? '' : 's'}` : span}
      </span>
      <span className={styles['tlTally']}>
        {GROUP_ORDER.filter((group) => tally.has(group)).map((group) => (
          <span key={group} className={styles['tlTallyItem']} style={{ color: toneVar(kindMeta(GROUP_KIND[group]).tone) }}>
            <KindIcon kind={GROUP_KIND[group]} size={9} />
            {tally.get(group)}
          </span>
        ))}
        {lane.undated > 0 ? (
          <span className={styles['tlUndated']} title="Rows with no event time — still listed in the card below">
            +{lane.undated} undated
          </span>
        ) : null}
      </span>
    </span>
  );
}

/* ---- the board ---------------------------------------------------------- */

export function Timeline({
  members,
  window: bounds,
}: {
  members: StandupMember[];
  /** Machine-readable window; both `""` on legacy reports (axis from events). */
  window?: { start: string; end: string } | undefined;
}) {
  // Legend filter: one selected group, everything else dims. Presentation
  // state only — dimmed marks stay in the page and in the accessibility tree.
  const [focus, setFocus] = useState<KindGroup | null>(null);

  const all = members.map(laneFor);
  const lanes = all.filter((lane) => lane.events.length > 0);
  if (!lanes.length) return null;
  // A member whose evidence is *entirely* undated — a tracker-heavy day of
  // carried WIP, which Jira and AzDO both ship with an empty timestamp — has no
  // marks to plot but must not vanish from the overview. They get a rail and an
  // empty track, which says "here, but nothing datable" rather than nothing.
  const undatedOnly = all.filter((lane) => !lane.events.length && lane.undated > 0);

  const scale = buildScale(
    lanes.flatMap((lane) => lane.events.map((event) => event.at)),
    bounds
  );
  const { multiDay } = scale;

  const threadInput: ThreadInput[] = lanes.flatMap((lane, index) =>
    lane.events
      .filter((event) => event.artifact)
      .map((event) => ({ lane: index, at: event.at, artifact: event.artifact }))
  );
  const { threads, dropped, partners } = buildThreads(threadInput);
  /** The other people on an artifact — stated in words, never by the line alone. */
  const partnersFor = (artifact: string, lane: number): string[] =>
    (partners.get(artifact) ?? [])
      .filter((other) => other !== lane)
      .map((other) => (lanes[other] as Lane).name);
  // Divided by every rendered row, not just the plotted ones: the overlay spans
  // the whole lane grid, so the undated-only lanes appended below still count.
  const rows = lanes.length + undatedOnly.length;
  const laneY = (index: number) => ((index + TRACK_CENTRE) / rows) * 100;

  const present = new Set(lanes.flatMap((lane) => lane.events.map((event) => kindGroup(event.kind))));
  const legend = GROUP_ORDER.filter((group) => present.has(group));

  return (
    <div className={styles['timeline']}>
      <div className={styles['tlHead']}>
        <Eyebrow>Activity timeline</Eyebrow>
        {/* The legend doubles as a filter: press a kind to spotlight it. The
            word always rides beside the icon — never a shape or colour alone. */}
        <div className={styles['tlLegend']} role="group" aria-label="Filter by activity kind">
          {legend.map((group) => {
            const meta = kindMeta(GROUP_KIND[group]);
            return (
              <button
                key={group}
                type="button"
                className={cx(styles['tlLegendItem'], focus !== null && focus !== group && styles['tlDimmed'])}
                style={{ color: toneVar(meta.tone) }}
                aria-pressed={focus === group}
                onClick={() => setFocus((current) => (current === group ? null : group))}
              >
                <KindIcon kind={GROUP_KIND[group]} size={10} />
                <span>{meta.label}</span>
              </button>
            );
          })}
        </div>
      </div>

      {/* The axis is not linear, and a capped drawing is not the whole picture.
          Saying both out loud is not optional — this panel already announces
          `+N undated` and labels every notch, and a silently dropped thread
          would be the one omission left. */}
      {scale.compressed || dropped > 0 ? (
        <p className={styles['tlNote']}>
          {scale.compressed ? 'Quiet stretches are compressed — each notch is labelled with how long it ran.' : ''}
          {scale.compressed && dropped > 0 ? ' ' : ''}
          {dropped > 0 ? `${dropped} further shared-work link${dropped === 1 ? '' : 's'} not drawn.` : ''}
        </p>
      ) : null}

      <div className={styles['tlAxis']}>
        <span aria-hidden="true" />
        <span className={styles['tlAxisTrack']} aria-hidden="true">
          {scale.ticks.map((tick) => (
            <span
              key={`${tick.at}-${tick.left}`}
              className={cx(styles['tlTick'], tick.day && styles['tlTickDay'])}
              style={{ left: `${tick.left}%` }}
            >
              {tick.label}
            </span>
          ))}
          {scale.gaps.map((gap) => (
            <span
              key={gap.left}
              className={styles['tlGapLabel']}
              style={{ left: `${gap.left + gap.width / 2}%` }}
            >
              {gap.label}
            </span>
          ))}
        </span>
      </div>

      {/* Two cells per row, all direct grid children, so every row shares the
          same resolved columns without needing subgrid support. */}
      <div className={styles['tlLanes']}>
        <svg
          className={styles['tlThreads']}
          viewBox="0 0 100 100"
          preserveAspectRatio="none"
          aria-hidden="true"
        >
          {threads.map((thread, index) => {
            const x1 = scale.pct(thread.fromAt);
            const x2 = scale.pct(thread.toAt);
            const y1 = laneY(thread.fromLane);
            const y2 = laneY(thread.toLane);
            // Both control points bulge the same way, and never by less than
            // BEND_MIN: two people touching one PR minutes apart would
            // otherwise draw a vertical line straight through the lanes
            // between them, which reads as a rendering artefact and not as a
            // relationship.
            const bend = Math.max(Math.abs(x2 - x1) * 0.35, BEND_MIN);
            return (
              <path
                key={index}
                className={styles['tlThread']}
                d={`M${x1} ${y1} C${x1 + bend} ${y1} ${x2 + bend} ${y2} ${x2} ${y2}`}
                vectorEffect="non-scaling-stroke"
              />
            );
          })}
        </svg>

        {lanes.map((lane, laneIndex) => {
          const marks = marksFor(lane.events, scale);
          const captions = placeCaptions(marks);
          const first = (marks[0] as Mark).pct;
          const last = (marks[marks.length - 1] as Mark).pct;
          return (
            <Fragment key={lane.name}>
              <Rail lane={lane} multiDay={multiDay} />
              <span className={styles['tlLane']}>
                <span className={styles['tlTrack']}>
                  {scale.gaps.map((gap) => (
                    <i
                      key={gap.left}
                      className={styles['tlGap']}
                      style={{ left: `${gap.left}%`, width: `${gap.width}%` }}
                      aria-hidden="true"
                    />
                  ))}
                  {marks.length > 1 ? (
                    <i
                      className={styles['tlSpine']}
                      style={{ left: `${first}%`, width: `${last - first}%` }}
                      aria-hidden="true"
                    />
                  ) : null}
                  {marks.map((mark, index) => {
                    const dimmed =
                      focus !== null && !mark.events.some((event) => kindGroup(event.kind) === focus);
                    const head = mark.events[0] as TimelineEvent;
                    return (
                      <span key={index} className={cx(styles['tlSlot'], dimmed && styles['tlDimmed'])}>
                        <MarkLink
                          mark={mark}
                          name={lane.name}
                          multiDay={multiDay}
                          partners={partnersFor(head.artifact, laneIndex)}
                        />
                      </span>
                    );
                  })}
                </span>
                <span className={styles['tlBand']} aria-hidden="true">
                  {captions.map((caption) => (
                    <span
                      key={caption.rank}
                      className={cx(
                        styles['tlCap'],
                        caption.counted && styles['tlCapCount'],
                        caption.rank >= CAPTION_LOW_RANK && styles['tlCapLow']
                      )}
                      style={{ left: `${caption.pct}%` }}
                    >
                      {caption.text}
                    </span>
                  ))}
                </span>
              </span>
            </Fragment>
          );
        })}

        {undatedOnly.map((lane) => (
          <Fragment key={lane.name}>
            <Rail lane={lane} multiDay={multiDay} />
            <span className={styles['tlLane']}>
              <span className={cx(styles['tlTrack'], styles['tlTrackEmpty'])} />
              <span className={styles['tlBand']} />
            </span>
          </Fragment>
        ))}
      </div>
    </div>
  );
}
