/**
 * The activity timeline — the standup's opening picture.
 *
 * One horizontal time axis across the activity window, one swimlane per
 * member, every dated evidence row a dot: what each person did, when, in what
 * order. Icons (KindIcon) distinguish commits from PRs from reviews from
 * ticket moves, so the day's shape reads before a single card is opened.
 *
 * Interactivity is pure client state — exports open over `file://` under a
 * CSP with `connect-src 'none'`, so tooltips are CSS on hover/focus, and a
 * dot is a plain `<a href="#m-…">` to the member's card below (the cards
 * already carry those ids for the jump strip). No fetch, ever.
 *
 * Placement rules for awkward data:
 * - `time: ""` rows (carried WIP, some tracker updates) are *excluded* from
 *   the plot — a dot at an invented position would be a lie on a time axis —
 *   and surface as a muted "+N undated" note; the rows stay fully visible in
 *   the card below. Nothing hidden, only folded.
 * - Events that fall outside the reported window (a PR's child commit can
 *   predate it) stretch the axis rather than clip: every dot is plotted.
 * - Timestamps arrive in a mix of naive/offset ISO forms; `Date.parse` reads
 *   naive ones as viewer-local, so cross-source ordering can shift by a tz
 *   delta. Accepted for v1 — dots may shift, never crash or vanish.
 */

import { Fragment, useState } from 'react';

import { Avatar, Eyebrow } from '../../design/primitives';
import { toneVar } from '../../design/tone';
import { cx } from '../../runtime/cx';
import type { EvidenceItem, StandupMember } from '../boot';
import { KindIcon, kindGroup, kindMeta, type KindGroup } from './KindIcon';
import styles from './timeline.module.css';

/** Anchor-safe member id shared with the card headers, `#m-ada-lovelace`. */
export function memberSlug(name: string): string {
  return (
    name
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '') || 'member'
  );
}

const HOUR = 3_600_000;
/** Above this span the axis switches from clock ticks to day ticks. */
const DAY_AXIS_SPAN = 30 * HOUR;
/** Events within this share of the track merge into one clustered dot. */
const CLUSTER_PCT = 1.8;
/** Tooltip rows a cluster lists before folding to "+N more". */
const CLUSTER_TIP_ROWS = 6;

interface TimelineEvent {
  kind: string;
  key: string;
  title: string;
  repo: string;
  status: string;
  at: number;
}

interface Lane {
  name: string;
  slug: string;
  events: TimelineEvent[];
  undated: number;
}

/** Naive stamps read as viewer-local; AzDO's space separator is normalised. */
function parseTime(value: string): number {
  if (!value) return NaN;
  return Date.parse(value.replace(' ', 'T'));
}

function two(n: number): string {
  return String(n).padStart(2, '0');
}

function fmtClock(at: number): string {
  const d = new Date(at);
  return `${two(d.getHours())}:${two(d.getMinutes())}`;
}

const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'] as const;

function fmtDay(at: number): string {
  const d = new Date(at);
  return `${WEEKDAYS[d.getDay()]} ${two(d.getMonth() + 1)}-${two(d.getDate())}`;
}

function fmtWhen(at: number, multiDay: boolean): string {
  return multiDay ? `${fmtDay(at)} ${fmtClock(at)}` : fmtClock(at);
}

/**
 * One member's dots: every category's evidence flattened, a PR's child
 * commits hoisted to their own dots (they are distinct work moments — the
 * clustering pass absorbs the density), deduped by the exporter's own rule.
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
      events.push({ kind: item.kind, key: item.key, title: item.title, repo: item.repo, status: item.status, at });
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
  return { name: member.name, slug: memberSlug(member.name), events, undated };
}

interface Tick {
  at: number;
  label: string;
  /** A local-midnight tick — its hairline extends down through the lanes. */
  day: boolean;
}

function axisTicks(start: number, end: number): Tick[] {
  const span = end - start;
  const out: Tick[] = [];
  if (span <= DAY_AXIS_SPAN) {
    const stepHours = [1, 2, 3, 6, 12].find((h) => span / (h * HOUR) <= 10) ?? 12;
    const first = new Date(start);
    first.setMinutes(0, 0, 0);
    while (first.getTime() < start || first.getHours() % stepHours !== 0) {
      first.setTime(first.getTime() + HOUR);
    }
    for (let t = first.getTime(); t <= end; t += stepHours * HOUR) {
      out.push({ at: t, label: fmtClock(t), day: false });
    }
    return out;
  }
  const cursor = new Date(start);
  cursor.setHours(0, 0, 0, 0);
  if (cursor.getTime() < start) cursor.setDate(cursor.getDate() + 1);
  const midnights: number[] = [];
  while (cursor.getTime() <= end) {
    midnights.push(cursor.getTime());
    cursor.setDate(cursor.getDate() + 1);
  }
  const step = Math.max(1, Math.ceil(midnights.length / 10));
  midnights.forEach((t, index) => {
    if (index % step === 0) out.push({ at: t, label: fmtDay(t), day: true });
  });
  return out;
}

interface Cluster {
  at: number;
  events: TimelineEvent[];
}

/** Greedy left-to-right sweep: near-simultaneous events share one dot. */
function clusterEvents(events: TimelineEvent[], span: number): Cluster[] {
  const threshold = span * (CLUSTER_PCT / 100);
  const out: Cluster[] = [];
  for (const event of events) {
    const last = out[out.length - 1];
    if (last && event.at - last.at <= threshold) {
      last.events.push(event);
      last.at = last.events.reduce((sum, e) => sum + e.at, 0) / last.events.length;
    } else {
      out.push({ at: event.at, events: [event] });
    }
  }
  return out;
}

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

/** What a dot calls its event. Doc keys are machine ids (a Confluence page
 * id, a Notion UUID) — same rule as EvidenceRow: the title is the handle and
 * the id never renders. */
function eventLabel(event: TimelineEvent): string {
  const meta = kindMeta(event.kind);
  if (meta.label === 'doc') return event.title || meta.label;
  return event.title || event.key || meta.label;
}

function clusterAria(cluster: Cluster, name: string, multiDay: boolean): string {
  if (cluster.events.length === 1) {
    const event = cluster.events[0] as TimelineEvent;
    const meta = kindMeta(event.kind);
    const parts = [fmtWhen(event.at, multiDay), event.status, event.repo].filter(Boolean).join(', ');
    return `${meta.label}: ${eventLabel(event)} — ${parts}. Jump to ${name}'s update.`;
  }
  const first = cluster.events[0] as TimelineEvent;
  const last = cluster.events[cluster.events.length - 1] as TimelineEvent;
  return (
    `${cluster.events.length} events between ${fmtWhen(first.at, multiDay)} and ` +
    `${fmtWhen(last.at, multiDay)}. Jump to ${name}'s update.`
  );
}

function Dot({ cluster, name, pct, multiDay }: { cluster: Cluster; name: string; pct: number; multiDay: boolean }) {
  const head = cluster.events[0] as TimelineEvent;
  const meta = kindMeta(head.kind);
  const count = cluster.events.length;
  // Tooltip alignment is decided from the position — no measurement JS.
  const align = pct < 15 ? styles['tipLeft'] : pct > 85 ? styles['tipRight'] : undefined;
  const shown = cluster.events.slice(0, CLUSTER_TIP_ROWS);
  const folded = count - shown.length;

  return (
    <a
      href={`#m-${memberSlug(name)}`}
      className={cx(styles['mark'], align)}
      style={{ left: `${pct}%`, color: toneVar(meta.tone) }}
      aria-label={clusterAria(cluster, name, multiDay)}
    >
      <KindIcon kind={head.kind} />
      {count > 1 ? <span className={styles['markBadge']}>×{count}</span> : null}
      {/* Presentation only — the aria-label above already says all of this. */}
      <span className={styles['tip']} aria-hidden="true">
        {count === 1 ? (
          <>
            <strong className={styles['tipTitle']}>{eventLabel(head)}</strong>
            {(meta.label !== 'doc' && head.key) || head.repo ? (
              <span className={styles['tipMeta']}>
                {[meta.label === 'doc' ? '' : head.key, head.repo].filter(Boolean).join(' · ')}
              </span>
            ) : null}
            <span className={styles['tipMeta']}>
              {[fmtWhen(head.at, multiDay), head.status].filter(Boolean).join(' · ')}
            </span>
          </>
        ) : (
          <>
            {shown.map((event, index) => (
              <span key={index} className={styles['tipRow']}>
                <i style={{ color: toneVar(kindMeta(event.kind).tone) }}>
                  <KindIcon kind={event.kind} size={10} />
                </i>
                <span className={styles['tipRowText']}>{eventLabel(event)}</span>
                <span className={styles['tipRowTime']}>{fmtWhen(event.at, multiDay)}</span>
              </span>
            ))}
            {folded > 0 ? <span className={styles['tipMeta']}>+{folded} more</span> : null}
          </>
        )}
      </span>
    </a>
  );
}

export function Timeline({ members, window: bounds }: {
  members: StandupMember[];
  /** Machine-readable window; both `""` on legacy reports (axis from events). */
  window?: { start: string; end: string } | undefined;
}) {
  // Legend filter: one selected group, everything else dims. Presentation
  // state only — dimmed dots stay in the page and in the accessibility tree.
  const [focus, setFocus] = useState<KindGroup | null>(null);

  const lanes = members.map(laneFor).filter((lane) => lane.events.length > 0);
  if (!lanes.length) return null;

  const times = lanes.flatMap((lane) => lane.events.map((event) => event.at));
  let start = parseTime(bounds?.start ?? '');
  let end = parseTime(bounds?.end ?? '');
  if (!Number.isFinite(start)) start = Math.min(...times);
  if (!Number.isFinite(end)) end = Math.max(...times);
  start = Math.min(start, ...times);
  end = Math.max(end, ...times);
  if (end - start < HOUR) {
    start -= HOUR;
    end += HOUR;
  }
  const pad = (end - start) * 0.02;
  start -= pad;
  end += pad;
  const span = end - start;
  const pct = (t: number) => ((t - start) / span) * 100;
  const multiDay = span > DAY_AXIS_SPAN;
  const ticks = axisTicks(start, end);
  const dayTicks = ticks.filter((tick) => tick.day);

  const present = new Set(lanes.flatMap((lane) => lane.events.map((event) => kindGroup(event.kind))));
  const legend = GROUP_ORDER.filter((group) => present.has(group));

  return (
    <div className={styles['timeline']}>
      <div className={styles['head']}>
        <Eyebrow>Activity timeline</Eyebrow>
        {/* The legend doubles as a filter: press a kind to spotlight it. The
            word always rides beside the icon — never a shape or colour alone. */}
        <div className={styles['legend']} role="group" aria-label="Filter by activity kind">
          {legend.map((group) => {
            const meta = kindMeta(GROUP_KIND[group]);
            return (
              <button
                key={group}
                type="button"
                className={cx(styles['legendItem'], focus !== null && focus !== group && styles['dimmed'])}
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

      {/* Three cells per row, all direct grid children, so every row shares
          the same resolved columns without needing subgrid support. */}
      <div className={styles['grid']}>
        <span aria-hidden="true" />
        <span className={styles['axisTrack']} aria-hidden="true">
          {ticks.map((tick) => (
            <span key={tick.at} className={styles['tick']} style={{ left: `${pct(tick.at)}%` }}>
              {tick.label}
            </span>
          ))}
        </span>
        <span aria-hidden="true" />

        {lanes.map((lane) => {
          const clusters = clusterEvents(lane.events, span);
          const first = pct((clusters[0] as Cluster).at);
          const last = pct((clusters[clusters.length - 1] as Cluster).at);
          return (
            <Fragment key={lane.name}>
              <span className={styles['laneName']}>
                <Avatar name={lane.name} size={18} />
                <span className={styles['laneWord']}>{lane.name}</span>
              </span>
              <span className={styles['track']}>
                {dayTicks.map((tick) => (
                  <i
                    key={tick.at}
                    className={styles['dayBreak']}
                    style={{ left: `${pct(tick.at)}%` }}
                    aria-hidden="true"
                  />
                ))}
                {clusters.length > 1 ? (
                  <i
                    className={styles['connector']}
                    style={{ left: `${first}%`, width: `${last - first}%` }}
                    aria-hidden="true"
                  />
                ) : null}
                {clusters.map((cluster, index) => {
                  const dimmed =
                    focus !== null && !cluster.events.some((event) => kindGroup(event.kind) === focus);
                  return (
                    <span key={index} className={cx(styles['markSlot'], dimmed && styles['dimmed'])}>
                      <Dot cluster={cluster} name={lane.name} pct={pct(cluster.at)} multiDay={multiDay} />
                    </span>
                  );
                })}
              </span>
              <span className={styles['laneEnd']}>
                {lane.undated > 0 ? (
                  <span
                    className={styles['undated']}
                    title="Rows with no event time — still listed in the member's card below"
                  >
                    +{lane.undated} undated
                  </span>
                ) : null}
              </span>
            </Fragment>
          );
        })}
      </div>
    </div>
  );
}
