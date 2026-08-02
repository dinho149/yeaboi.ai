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

import { useState } from 'react';

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
import { cx } from '../../runtime/cx';
import type {
  EditMap,
  EvidenceLink,
  Run,
  StandupCategory,
  StandupMember,
  StandupPractice,
  Trend,
} from '../boot';
import { EditableSlot } from '../editing/Editable';
import { Field } from '../editing/Field';
import { votePractice, type Verdict } from '../vote';
import { EvidenceList } from './Evidence';
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
/** Payload category labels → the same tones, so the count chips, the activity
 * bars, and the category blocks all speak one colour language. */
const CATEGORY_TONE_BY_LABEL: Record<string, Tone> = {
  Ticketing: 'accent',
  Code: 'accent2',
  Documentation: 'info',
};
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

/** Anchor-safe member id for the jump strip, `#m-ada-lovelace`. */
function memberSlug(name: string): string {
  return (
    name
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '') || 'member'
  );
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

function Category({ category, slug }: { category: StandupCategory; slug: string }) {
  const categoryTone = tone(CATEGORY_TONE_BY_LABEL, category.label);
  const evidence = category.evidence ?? [];
  return (
    <div className={styles['category']} style={{ borderLeftColor: toneVar(categoryTone) }}>
      <span className={styles['categoryHead']}>
        {/* The tone anchors the block to its count chip and activity-bar segment;
            the Eyebrow word rides beside the colour, per the house rule. */}
        <i className={styles['dot']} style={{ background: toneVar(categoryTone) }} aria-hidden="true" />
        <Eyebrow>{category.label}</Eyebrow>
      </span>
      {category.items.length ? (
        <ul className={styles['bullets']}>
          {category.items.map((runs, index) => (
            <li key={index}>
              <RichText runs={runs} />
            </li>
          ))}
        </ul>
      ) : null}
      {evidence.length ? (
        <EvidenceList items={evidence} id={`ev-${slug}-${category.label.toLowerCase()}`} />
      ) : (
        // Legacy reports predate structured evidence — keep their chips.
        <Links links={category.links} />
      )}
    </div>
  );
}

/**
 * Practice signals — the deterministic coaching notes from `standup/habits.py`.
 *
 * Tone by rule, with the same `?? 'low'` fallback the confidence and coverage
 * maps use: rule ids are *produced* by the engine, not validated against
 * anything, so one this bundle has never heard of renders muted instead of
 * failing a build. The label comes down in the payload rather than living here
 * twice — the engine owns that vocabulary.
 *
 * Nothing here is louder than the blocker chip above it. These are nudges.
 */
const PRACTICE_TONE: Record<string, Tone> = {
  'untracked-work': 'warn',
  'untracked-docs': 'warn',
  'board-not-updated': 'warn',
  'wip-sprawl': 'info',
  'large-change': 'info',
  'no-pull-request': 'warn',
  'commit-messages': 'low',
};

/**
 * One signal, plus — on a correctable share — the two answers to it.
 *
 * The person best placed to say "that PR is the spike ticket, it just doesn't
 * name it" is usually the teammate reading this, not the host at their terminal.
 * So the controls live next to the claim they dispute.
 *
 * A thumbs-down asks for an optional reason *before* sending, rather than after.
 * Sending twice would not work: the first call removes the signal from the run,
 * so a follow-up carrying the note would find nothing to attach it to.
 */
function Practice({
  member,
  practice,
  correctable,
}: {
  member: string;
  practice: StandupPractice;
  correctable: boolean;
}) {
  const [state, setState] = useState<'idle' | 'asking' | 'sending' | 'done'>('idle');
  const [note, setNote] = useState('');
  const [outcome, setOutcome] = useState('');
  const [hidden, setHidden] = useState(false);

  const send = async (verdict: Verdict, why: string) => {
    setState('sending');
    try {
      const result = await votePractice(member, practice.rule, verdict, why);
      if (!result.applied) {
        setOutcome(result.reason || 'That signal has already been answered.');
        setState('done');
        return;
      }
      if (verdict === 'down') setHidden(true);
      else setOutcome('Thanks — confirmed.');
      setState('done');
    } catch {
      // fetch rejects only on transport failure: the host closed the share, or
      // the tunnel dropped. Say so plainly rather than looking like a refusal.
      setOutcome('Could not reach the standup — it may have stopped sharing.');
      setState('done');
    }
  };

  // A thumbs-down removes the signal from the report itself, so the card goes
  // with it. The line that replaces it is the receipt.
  if (hidden) {
    return (
      <li className={cx(styles['practice'], styles['practiceResolved'])}>
        Hidden — thanks. It won&rsquo;t come back.
      </li>
    );
  }

  return (
    <li className={styles['practice']}>
      <Chip tone={tone(PRACTICE_TONE, practice.rule)}>{practice.title}</Chip>
      {practice.repeat ? <span className={styles['practiceRepeat']}>again today</span> : null}
      <RichText runs={practice.detail} />
      <Links links={practice.evidence} />
      {correctable && state !== 'done' ? (
        state === 'asking' ? (
          <div className={styles['practiceAsk']}>
            <label className={styles['practiceAskLabel']} htmlFor={`why-${member}-${practice.rule}`}>
              What did we get wrong? (optional)
            </label>
            <input
              id={`why-${member}-${practice.rule}`}
              className={styles['practiceInput']}
              value={note}
              maxLength={500}
              placeholder="e.g. that PR is the spike ticket, it just doesn't name it"
              onInput={(event) => setNote((event.target as HTMLInputElement).value)}
            />
            <div className={styles['practiceAskRow']}>
              <button type="button" className={styles['practiceSend']} onClick={() => void send('down', note)}>
                Send
              </button>
              <button type="button" className={styles['practiceVote']} onClick={() => setState('idle')}>
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <p className={styles['practiceVotes']}>
            <span className={styles['practiceAskLabel']}>Is this right?</span>
            <button
              type="button"
              className={styles['practiceVote']}
              disabled={state === 'sending'}
              onClick={() => void send('up', '')}
            >
              <span aria-hidden="true">▲</span> Yes
            </button>
            <button
              type="button"
              className={styles['practiceVote']}
              disabled={state === 'sending'}
              onClick={() => setState('asking')}
            >
              <span aria-hidden="true">▼</span> No, and hide it
            </button>
          </p>
        )
      ) : null}
      {outcome ? <p className={styles['practiceOutcome']}>{outcome}</p> : null}
    </li>
  );
}

function Practices({
  member,
  practices,
  correctable,
}: {
  member: string;
  practices: StandupPractice[];
  correctable: boolean;
}) {
  if (!practices.length) return null;
  return (
    <div className={styles['practices']}>
      <Eyebrow>Practices</Eyebrow>
      <ul className={styles['practiceList']}>
        {practices.map((practice) => (
          <Practice
            key={practice.rule}
            member={member}
            practice={practice}
            correctable={correctable}
          />
        ))}
      </ul>
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

function Member({ member, correctable }: { member: StandupMember; correctable: boolean }) {
  const chips = member.counts
    .map((count, i) => {
      const [singular, plural] = CATEGORY_NOUNS[i] as readonly [string, string];
      return { count, noun: count === 1 ? singular : plural, chipTone: CATEGORY_TONES[i] as Tone };
    })
    .filter(({ count }) => count > 0);
  const slug = memberSlug(member.name);

  return (
    <div
      id={`m-${slug}`}
      className={member.blockers ? `${styles['member']} ${styles['blocked']}` : styles['member']}
    >
      <div className={styles['memberHead']}>
        <span className={styles['person']}>
          <Avatar name={member.name} size={26} />
          <strong>{member.name}</strong>
          {member.own ? <Chip tone="accent">you</Chip> : null}
        </span>
        <div className={styles['chips']}>
          {chips.map(({ count, noun, chipTone }) => (
            <Chip key={noun} tone={chipTone}>
              {count} {noun}
            </Chip>
          ))}
          {member.blockers ? <Chip tone="danger">blocked</Chip> : null}
        </div>
      </div>

      {/* The blocker leads the card: it is the one thing on this page somebody
          has to act on, and it must not sit below three categories of prose. */}
      {member.blockers ? (
        <Field edit={member.edit} field="blockers" label={`${member.name}'s blocker`}>
          <Note label="Blocker" runs={member.blockers} tone="danger" />
        </Field>
      ) : null}
      <Field edit={member.edit} field="summary" label={`${member.name}'s summary`}>
        {member.summary.length ? (
          <ul className={styles['memberSummary']}>
            {member.summary.map((runs, index) => (
              <li key={index}>
                <RichText runs={runs} />
              </li>
            ))}
          </ul>
        ) : (
          <p className={styles['memberSummary']}>No activity detected.</p>
        )}
      </Field>
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
            <Category key={category.label} category={category} slug={slug} />
          ))}
        </div>
      ) : null}
      {member.footnotes.map((footnote) => (
        <p key={footnote.label} className={styles['footnote']}>
          {footnote.label} — <RichText runs={footnote.runs} />
        </p>
      ))}

      {/* After the categories: the reader has just seen what shipped, which is
          the context that makes "and no ticket behind it" mean anything. */}
      {member.practices?.length ? (
        <Practices member={member.name} practices={member.practices} correctable={correctable} />
      ) : null}

      {member.outlook ? (
        <Field edit={member.edit} field="outlook" label={`${member.name}'s outlook`}>
          <Note label="Outlook" runs={member.outlook} />
        </Field>
      ) : null}
      {/* Renders nothing without a session, so a file on disk is unaffected. */}
      <EditableSlot anchor={member.anchor ?? ''} label={`${member.name}'s update`} />
      {member.selfReport ? (
        <p className={styles['quote']}>
          <span aria-hidden="true">✍</span> <RichText runs={member.selfReport} />
        </p>
      ) : null}
    </div>
  );
}

/**
 * Who's in this standup, at a glance — avatar pills that jump to each card.
 * Blocked members are marked with the word, not the ring alone.
 */
function MemberStrip({ members }: { members: StandupMember[] }) {
  if (members.length < 2) return null;
  return (
    <nav className={styles['memberStrip']} aria-label="Jump to member">
      {members.map((member) => (
        <a
          key={member.name}
          className={cx(styles['memberJump'], member.blockers && styles['memberJumpBlocked'])}
          href={`#m-${memberSlug(member.name)}`}
        >
          <Avatar name={member.name} size={18} />
          <span>{member.name}</span>
          {member.blockers ? <span className={styles['memberJumpFlag']}>blocked</span> : null}
        </a>
      ))}
    </nav>
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
  edit,
  summary,
  members,
  activityCounts,
  activityWindow,
  coverage,
  skipped,
  practices,
  images,
  trend,
  warnings,
  correctable = false,
}: {
  sprint: { name: string; day: number; total: number };
  confidence: { label: string; pct: number; text: string; trend: string; trendText: string; rationale: string };
  /** Report-level editable fields. Undefined on a file export. */
  edit?: EditMap;
  summary: Run[][];
  members: StandupMember[];
  activityCounts: Array<[string, number]>;
  activityWindow: string;
  coverage: Array<[string, string]>;
  skipped: Array<[string, string]>;
  practices: Array<{ rule: string; count: number; title: string }>;
  images: string[];
  trend: Trend | null;
  warnings: string[];
  /** A share server is behind this page and will accept a verdict. */
  correctable?: boolean;
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
          {confidence.rationale ? (
            <Field edit={edit} field="confidence_rationale" label="the confidence rationale" inline>
              <span className={styles['rationale']}>{confidence.rationale}</span>
            </Field>
          ) : null}
        </p>

        <TrendCard trend={trend} endTone={confidenceTone} />
        <TeamActivity members={members} />
        {practices?.length ? (
          <p className={styles['practiceRollup']}>
            <Eyebrow>Practices</Eyebrow>
            {practices.map(({ rule, count, title }) => (
              // The count is MEMBERS. Spelled out rather than left as a bare
              // number beside a colour, so it can't be read as a score.
              <Chip key={rule} tone={tone(PRACTICE_TONE, rule)}>
                {title} · {count} {count === 1 ? 'member' : 'members'}
              </Chip>
            ))}
          </p>
        ) : null}
        <NoticeBlock title="Notices" items={warnings} />
      </section>

      {summary.length ? (
        <section id="summary">
          <h2 className={styles['h2']}>Team Summary</h2>
          {/* The editor opens on the raw artifact string; what is drawn here is
              the derived view — sentences of link-runs, with no inverse. That
              asymmetry is the whole reason the payload carries {path, value}
              beside the region instead of expecting this side to unpick it. */}
          <Field edit={edit} field="team_summary" label="the team summary">
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
          </Field>
        </section>
      ) : null}

      <section id="updates">
        <h2 className={styles['h2']}>Updates</h2>
        <MemberStrip members={members} />
        {members.length ? (
          members.map((member) => (
            <Member key={member.name} member={member} correctable={correctable} />
          ))
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
