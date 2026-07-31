/**
 * The daily standup export.
 *
 * This is the report that gets read fastest and by the most people, so it is
 * built to be skimmed: the numbers first, then one sentence per fact in the
 * team summary, then a card per person that you only read if their name came
 * up. The member card's category grid is adaptive — a category with no activity
 * collapses to a footnote so the busy ones keep their width.
 *
 * **Everything here that used to be a colour in Python is a tone lookup now.**
 * `standup/export.py` held three maps — confidence label → CSS token,
 * confidence label → chip kind, coverage status → dot token — which were one
 * fact written three ways for three different markup helpers. They are two
 * `Record`s here, both with a fallback, because neither vocabulary is validated
 * against untrusted input: the engine *produces* these strings, so an
 * unrecognised one should go muted rather than fail a build.
 */

import {
  Avatar,
  Chip,
  countedSegments,
  Eyebrow,
  Legend,
  NoticeBlock,
  RichText,
  SegmentBar,
  StatBar,
  StatGrid,
  StatTile,
} from '../../design/primitives';
import { toneVar, type Tone } from '../../design/tone';
import type { EvidenceLink, Run, StandupCategory, StandupMember, Trend } from '../boot';
import styles from './reports.module.css';
import { TrendCard } from './Trend';

/** `confidence.LABEL_*` from `standup/confidence.py`. Unknown → muted. */
const CONFIDENCE_TONE: Record<string, Tone> = {
  'On track': 'ok',
  'At risk': 'warn',
  Behind: 'danger',
  'Insufficient data': 'low',
};

/** `category_coverage` statuses. The word always rides beside the dot — never colour alone. */
const COVERAGE_TONE: Record<string, Tone> = {
  covered: 'ok',
  partial: 'warn',
  failed: 'danger',
  not_configured: 'low',
};

/** The three activity categories, in the order every part of this page uses. */
const CATEGORY_TONES: readonly Tone[] = ['accent', 'accent2', 'info'];
/** Legend headings. Always plural — they label a series, not a count. */
const CATEGORY_LABELS = ['Tickets', 'Code', 'Docs'] as const;
/** `[singular, plural]` for the count chips. "Code" is uncountable either way. */
const CATEGORY_NOUNS: ReadonlyArray<readonly [string, string]> = [
  ['ticket', 'tickets'],
  ['code', 'code'],
  ['doc', 'docs'],
];

function tone(map: Record<string, Tone>, key: string): Tone {
  return map[key] ?? 'low';
}

/** Evidence that is not already an inline link in the prose, as chips. */
function Links({ links }: { links: EvidenceLink[] }) {
  if (!links.length) return null;
  return (
    <div className={styles['chipRow']}>
      {links.map(([label, url]) => (
        <Chip key={`${label}-${url}`} {...(url ? { href: url } : {})}>
          {label}
        </Chip>
      ))}
    </div>
  );
}

function Category({ category }: { category: StandupCategory }) {
  return (
    <div className={styles['category']}>
      <Eyebrow>{category.label}</Eyebrow>
      {category.items.length ? (
        <ul className={styles['bullets']}>
          {category.items.map((runs, index) => (
            <li key={index}>
              <RichText runs={runs} />
            </li>
          ))}
        </ul>
      ) : null}
      <Links links={category.links} />
    </div>
  );
}

/** A labelled one-liner: "Outlook", "Blocker", "Since last standup". */
function Note({ label, runs, tone: chipTone }: { label: string; runs: Run[]; tone?: Tone }) {
  return (
    <p className={styles['note']}>
      <Chip {...(chipTone ? { tone: chipTone } : {})}>{label}</Chip>
      <RichText runs={runs} />
    </p>
  );
}

function Member({ member }: { member: StandupMember }) {
  const chips = member.counts
    .map((count, i) => {
      const [singular, plural] = CATEGORY_NOUNS[i] as readonly [string, string];
      return { count, noun: count === 1 ? singular : plural };
    })
    .filter(({ count }) => count > 0);

  return (
    <div className={member.blockers ? `${styles['member']} ${styles['blocked']}` : styles['member']}>
      <div className={styles['memberHead']}>
        <span className={styles['person']}>
          <Avatar name={member.name} size={26} />
          <strong>{member.name}</strong>
          {member.own ? <Chip tone="accent">you</Chip> : null}
        </span>
        <div className={styles['chips']}>
          {chips.map(({ count, noun }) => (
            <Chip key={noun}>
              {count} {noun}
            </Chip>
          ))}
          {member.blockers ? <Chip tone="danger">blocked</Chip> : null}
        </div>
      </div>

      <p className={styles['memberSummary']}>
        {member.summary.length ? <RichText runs={member.summary} /> : 'No activity detected.'}
      </p>
      {member.progressNote ? (
        <p className={styles['since']}>
          <span aria-hidden="true">↺</span> <em>Since last standup:</em>{' '}
          <RichText runs={member.progressNote} />
        </p>
      ) : null}
      <Links links={member.links} />

      {member.categories.length ? (
        <div className={styles['categories']}>
          {member.categories.map((category) => (
            <Category key={category.label} category={category} />
          ))}
        </div>
      ) : null}
      {member.footnotes.map((footnote) => (
        <p key={footnote.label} className={styles['footnote']}>
          {footnote.label} — <RichText runs={footnote.runs} />
        </p>
      ))}

      {member.outlook ? <Note label="Outlook" runs={member.outlook} /> : null}
      {member.blockers ? <Note label="Blocker" runs={member.blockers} tone="danger" /> : null}
      {member.selfReport ? (
        <p className={styles['quote']}>
          <span aria-hidden="true">✍</span> <RichText runs={member.selfReport} />
        </p>
      ) : null}
    </div>
  );
}

/** Per-member stacked bars, each scaled against the busiest member. */
function TeamActivity({ members }: { members: StandupMember[] }) {
  const rows = members
    .map((m) => ({ name: m.name, counts: m.counts, total: m.counts[0] + m.counts[1] + m.counts[2] }))
    .filter((row) => row.total > 0);
  if (!rows.length) return null;
  const busiest = Math.max(...rows.map((row) => row.total));

  return (
    <div className={styles['activity']}>
      <Eyebrow>Team activity</Eyebrow>
      <Legend items={CATEGORY_LABELS.map((label, i) => ({ label, tone: CATEGORY_TONES[i] as Tone }))} />
      {rows.map((row) => (
        <div key={row.name} className={styles['activityRow']}>
          <span className={styles['activityName']}>{row.name}</span>
          <SegmentBar
            segments={row.counts.map((value, i) => ({ value, tone: CATEGORY_TONES[i] as Tone }))}
            label={`${row.name}: ${row.total} activity item(s)`}
            // Scaled against the busiest member rather than each filling the
            // track: bars that all reach the end are four pictures of 100%.
            widthPct={(row.total / busiest) * 100}
          />
          <span className={styles['activityTotal']}>{row.total}</span>
        </div>
      ))}
    </div>
  );
}

export function Standup({
  sprint,
  confidence,
  summary,
  members,
  activityCounts,
  activityWindow,
  coverage,
  skipped,
  images,
  trend,
  warnings,
}: {
  sprint: { name: string; day: number; total: number };
  confidence: { label: string; pct: number; text: string; trend: string; trendText: string; rationale: string };
  summary: Run[][];
  members: StandupMember[];
  activityCounts: Array<[string, number]>;
  activityWindow: string;
  coverage: Array<[string, string]>;
  skipped: Array<[string, string]>;
  images: string[];
  trend: Trend | null;
  warnings: string[];
}) {
  const confidenceTone = tone(CONFIDENCE_TONE, confidence.label);
  const known = confidence.label && confidence.label !== 'Insufficient data';
  const totalActivity = activityCounts.reduce((sum, [, n]) => sum + n, 0);
  const sources = countedSegments(activityCounts);
  const hasDetails = activityCounts.length > 0 || coverage.length > 0 || skipped.length > 0;

  return (
    <>
      <section id="overview">
        <h2 className={styles['h2']}>Overview</h2>
        <StatGrid>
          {sprint.total ? (
            <StatTile value={`${sprint.day} / ${sprint.total}`} label="Sprint day" hint={sprint.name}>
              <StatBar
                pct={(sprint.day / sprint.total) * 100}
                label={`Sprint day ${sprint.day} of ${sprint.total}`}
              />
            </StatTile>
          ) : (
            <StatTile value={sprint.name || '—'} label="Sprint" />
          )}
          <StatTile value={known ? `${confidence.pct}%` : '—'} label="Confidence" tone={confidenceTone} />
          <StatTile value={members.length} label="Members" />
          {activityCounts.length ? <StatTile value={totalActivity} label="Activity items" /> : null}
        </StatGrid>

        <p className={styles['confidence']}>
          <Chip tone={confidenceTone}>{confidence.text}</Chip>
          {confidence.trendText ? (
            <Chip tone={confidence.trend === 'improving' ? 'ok' : 'danger'}>{confidence.trendText}</Chip>
          ) : null}
          {confidence.rationale ? <span className={styles['rationale']}>{confidence.rationale}</span> : null}
        </p>

        <TrendCard trend={trend} endTone={confidenceTone} />
        <TeamActivity members={members} />
        <NoticeBlock title="Notices" items={warnings} />
      </section>

      {summary.length ? (
        <section id="summary">
          <h2 className={styles['h2']}>Team Summary</h2>
          {/* One sentence is a paragraph; several are a scannable list. */}
          {summary.length === 1 ? (
            <p className={styles['lede']}>
              <RichText runs={summary[0] as Run[]} />
            </p>
          ) : (
            <ul className={styles['bullets']}>
              {summary.map((runs, index) => (
                <li key={index}>
                  <RichText runs={runs} />
                </li>
              ))}
            </ul>
          )}
        </section>
      ) : null}

      <section id="updates">
        <h2 className={styles['h2']}>Updates</h2>
        {members.length ? (
          members.map((member) => <Member key={member.name} member={member} />)
        ) : (
          <p className={styles['empty']}>No individual updates.</p>
        )}
      </section>

      {images.length ? (
        <section id="screenshots">
          <h2 className={styles['h2']}>Screenshots</h2>
          {images.map((src, index) => (
            <img key={index} className={styles['screenshot']} src={src} alt="Screenshot" />
          ))}
        </section>
      ) : null}

      {hasDetails ? (
        <section id="details">
          <h2 className={styles['h2']}>Details</h2>
          {sources.segments.length ? (
            <div className={styles['split']}>
              <SegmentBar segments={sources.segments} label="Activity by source" />
              <Legend items={sources.legend} />
            </div>
          ) : null}
          <ul className={styles['details']}>
            {activityCounts.length ? (
              <li>
                Activity examined — {activityCounts.map(([src, n]) => `${src}: ${n}`).join(', ')}
                {activityWindow ? ` (${activityWindow})` : ''}
              </li>
            ) : null}
            {coverage.length ? (
              <li>
                Coverage —{' '}
                {coverage.map(([category, status]) => (
                  <span key={category} className={styles['coverage']}>
                    {/* The status word always rides beside the dot: colour
                        alone is not a signal every reader can receive. */}
                    <i
                      className={styles['dot']}
                      style={{ background: toneVar(tone(COVERAGE_TONE, status)) }}
                      aria-hidden="true"
                    />
                    {category} <span className={styles['dim']}>{status.replace(/_/g, ' ')}</span>
                  </span>
                ))}
              </li>
            ) : null}
            {skipped.length ? (
              <li>Sources skipped — {skipped.map(([src, reason]) => `${src} (${reason})`).join(', ')}</li>
            ) : null}
          </ul>
        </section>
      ) : null}
    </>
  );
}
