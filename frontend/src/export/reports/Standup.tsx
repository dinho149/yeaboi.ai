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

import type { ReactNode } from 'react';
import { useState } from 'react';

import {
  Avatar,
  Chip,
  countedSegments,
  Eyebrow,
  Legend,
  Lozenge,
  NoticeBlock,
  RichText,
  SegmentBar,
  StatBar,
  StatGrid,
  StatTile,
  type LozengeCategory,
} from '../../design/primitives';
import { toneVar, type Tone } from '../../design/tone';
import { cx } from '../../runtime/cx';
import { safeUrl } from '../../runtime/url';
import type {
  EditMap,
  EvidenceItem,
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
import { EvidenceList, EvidenceRow, statusCategory, VISIBLE_ROWS, type EvidenceVariant } from './Evidence';
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

/**
 * One full-width labelled block of the issue card. Jira's issue view is a
 * single column of headed sections, so every fact lands under a heading a
 * tracker user already knows how to scan for.
 */
function CardSection({ label, tone: sectionTone, children }: { label: string; tone?: Tone; children: ReactNode }) {
  return (
    <section className={styles['issueSection']}>
      <span className={styles['categoryHead']}>
        {/* The tone anchors the section to its count chip and activity-bar
            segment; the Eyebrow word rides beside the colour, per the house rule. */}
        {sectionTone ? (
          <i className={styles['dot']} style={{ background: toneVar(sectionTone) }} aria-hidden="true" />
        ) : null}
        <Eyebrow>{label}</Eyebrow>
      </span>
      {children}
    </section>
  );
}

function Category({ category, slug, label, variant, evidence: evidenceOverride }: {
  category: StandupCategory;
  slug: string;
  /** Overrides the payload label — "Ticketing" renders as "Ticket status changes". */
  label?: string;
  variant?: EvidenceVariant;
  /** Overrides the payload evidence — the rows a story section did NOT claim. */
  evidence?: EvidenceItem[];
}) {
  const categoryTone = tone(CATEGORY_TONE_BY_LABEL, category.label);
  const evidence = evidenceOverride ?? category.evidence ?? [];
  // A member whose stories claimed every code row (and whose LLM prose came
  // back empty) has nothing left to say here — a bare heading is not a section.
  if (!category.items.length && !evidence.length && (evidenceOverride || !category.links.length)) return null;
  return (
    <CardSection label={label ?? category.label} tone={categoryTone}>
      {category.items.length ? (
        <ul className={styles['bullets']}>
          {category.items.flatMap(splitTicketBullets).map((runs, index) => (
            <li key={index}>
              <RichText runs={runs} />
            </li>
          ))}
        </ul>
      ) : null}
      {evidence.length ? (
        <EvidenceList
          items={evidence}
          id={`ev-${slug}-${category.label.toLowerCase()}`}
          {...(variant ? { variant } : {})}
        />
      ) : evidenceOverride ? null : ( // an empty override means the stories claimed every row — nothing is missing
        // Legacy reports predate structured evidence — keep their chips.
        <Links links={category.links} />
      )}
    </CardSection>
  );
}

/** Kinds whose `key` IS a tracker handle — the twin of `KIND_META`'s ticket
 * rows. An AzDO work item's key is "#123", so the header cannot filter on key
 * shape alone: that spelling is also a PR number, and kind is what tells them
 * apart. */
const TRACKER_KINDS = new Set(['issue', 'update', 'comment', 'work_item', 'ticket', 'wip']);

/** A linked run that is a ticket key *plus* its title ("PSOT-1638 Barbican…"),
 * as opposed to a bare key mentioned mid-sentence. */
const TICKET_TITLE_LINK = /^[A-Za-z][A-Za-z0-9]+-\d+\s+\S/;

/** A text run that is nothing but list glue once a bullet ends: ", ", ", and ". */
const SEPARATOR_TAIL = /(?:[\s,;]|\band\b)+$/i;

/**
 * One ticket per bullet. The engine's prose enumerates tickets in a clause —
 * "Edited PSOT-1638 …, PSOT-1633 …, PSOT-1634 …" — which reads as a wall of
 * pink. A new bullet starts at every linked run that carries a key *and* a
 * title; the list glue left dangling at the previous bullet's end is trimmed.
 * Bare-key enumerations ("across six tickets: PSOT-1638, PSOT-1633, …") stay
 * one bullet: splitting those would make bullets of naked keys, and the feed
 * rows below already give each ticket its line.
 */
function splitTicketBullets(runs: Run[]): Run[][] {
  const bullets: Run[][] = [];
  let current: Run[] = [];
  // Whether the bullet being built already holds a titled ticket link. Intro
  // words ("Edited", "also edited") must stay glued to their first ticket, so
  // a titled link only opens a new bullet when one is already on the line.
  let hasTicket = false;

  const close = () => {
    const last = current[current.length - 1];
    if (last && !last.href) {
      const trimmed = last.s.replace(SEPARATOR_TAIL, '');
      if (trimmed) current[current.length - 1] = { ...last, s: trimmed };
      else current.pop();
    }
    if (current.length) bullets.push(current);
    current = [];
    hasTicket = false;
  };

  for (const run of runs) {
    if (run.href && TICKET_TITLE_LINK.test(run.s)) {
      if (hasTicket) close();
      hasTicket = true;
    }
    current.push(run);
  }
  close();
  return bullets.length ? bullets : [runs];
}

/**
 * The member's headline ticket, for the issue-key slot of the card header.
 * First ticketing evidence whose key looks like a tracker key; when none
 * exists the slot stays empty — a fabricated key would be a lie a Jira user
 * in particular would try to click.
 */
function topTicket(member: StandupMember): EvidenceItem | null {
  const rows = member.categories.find((c) => c.label === 'Ticketing')?.evidence ?? [];
  const isTicket = (item: EvidenceItem) => Boolean(item.key) && TRACKER_KINDS.has(item.kind);
  // A story outranks a fresher subtask: the header names the unit of work.
  return rows.find((item) => isTicket(item) && !item.subtask) ?? rows.find(isTicket) ?? null;
}

/**
 * One user story and everything the member did on it: the story's own ticket
 * row, each subtask on its own line, and the code/doc changes whose text names
 * the story or one of its subtasks.
 */
export interface StoryGroup {
  /** The top-level ticketing row — or a promoted orphan subtask. */
  story: EvidenceItem;
  /** A subtask whose parent row is not on this card; named, never fabricated. */
  orphan: boolean;
  subtasks: EvidenceItem[];
  code: EvidenceItem[];
  docs: EvidenceItem[];
}

/**
 * Nest the member's flat evidence into story groups, client-side — the payload
 * ships facts (`subtask`, `parent`, `tickets`), never layout.
 *
 * A row nests under another ONLY when the tracker itself said it is a subtask
 * AND its parent row is visible here: a team-managed Jira Story also carries a
 * `parent` (its epic), and type-blind nesting would file it under the epic as
 * if it were a subtask. Orphan subtasks (parent row absent) stay top-level as
 * their own group so the work never disappears. Code/doc rows attach by exact
 * reference only — the first key in `tickets` found among the visible stories
 * and subtasks; everything unreferenced is returned loose for the plain Code /
 * Documentation sections.
 */
export function groupStories(
  ticketing: EvidenceItem[],
  code: EvidenceItem[],
  docs: EvidenceItem[],
): { groups: StoryGroup[]; looseCode: EvidenceItem[]; looseDocs: EvidenceItem[] } {
  const groups: StoryGroup[] = [];
  const byStoryKey = new Map<string, StoryGroup>();
  for (const row of ticketing) {
    if (row.subtask) continue;
    const group: StoryGroup = { story: row, orphan: false, subtasks: [], code: [], docs: [] };
    groups.push(group);
    // First row wins on a duplicate key — upstream already deduped by URL.
    if (row.key && !byStoryKey.has(row.key)) byStoryKey.set(row.key, group);
  }
  // attach index: a change naming a subtask belongs to that subtask's story.
  const byAnyKey = new Map(byStoryKey);
  for (const row of ticketing) {
    if (!row.subtask) continue;
    const parent = row.parent ? byStoryKey.get(row.parent) : undefined;
    if (parent) {
      parent.subtasks.push(row);
      if (row.key && !byAnyKey.has(row.key)) byAnyKey.set(row.key, parent);
    } else {
      const group: StoryGroup = { story: row, orphan: true, subtasks: [], code: [], docs: [] };
      groups.push(group);
      if (row.key && !byAnyKey.has(row.key)) byAnyKey.set(row.key, group);
    }
  }
  const claim = (rows: EvidenceItem[], slot: 'code' | 'docs'): EvidenceItem[] =>
    rows.filter((row) => {
      const key = (row.tickets ?? []).find((ticket) => byAnyKey.has(ticket));
      if (!key) return true;
      (byAnyKey.get(key) as StoryGroup)[slot].push(row);
      return false;
    });
  return { groups, looseCode: claim(code, 'code'), looseDocs: claim(docs, 'docs') };
}

/** Whether the payload carries any hierarchy worth drawing as story groups —
 * without it the flat ticket feed is the honest (and legacy-identical) view. */
function hasHierarchy(grouped: ReturnType<typeof groupStories>): boolean {
  return grouped.groups.some(
    (group) => group.orphan || group.subtasks.length > 0 || group.code.length > 0 || group.docs.length > 0,
  );
}

/** One story group: the story row, one line per subtask, then its code/docs. */
function StorySection({ group, slug, index }: { group: StoryGroup; slug: string; index: number }) {
  const idBase = `ev-${slug}-story-${index}`;
  return (
    <li className={styles['storyCard']}>
      <ul className={styles['evidence']}>
        <EvidenceRow item={group.story} idBase={idBase} variant="feed" />
      </ul>
      {group.orphan && group.story.parent ? (
        // Plain text, not a link: the parent row is not in the evidence, and a
        // minted URL would be a guess.
        <p className={styles['storyParentNote']}>under {group.story.parent}</p>
      ) : null}
      {group.subtasks.length ? (
        <ul className={styles['storySubtasks']} aria-label={`Subtasks of ${group.story.key}`}>
          {group.subtasks.map((subtask, subtaskIndex) => (
            <EvidenceRow
              key={`${subtask.key}-${subtaskIndex}`}
              item={subtask}
              idBase={`${idBase}-sub${subtaskIndex}`}
              variant="feed"
            />
          ))}
        </ul>
      ) : null}
      {group.code.length ? (
        <div className={styles['storyLinkedWork']}>
          <Eyebrow>Code</Eyebrow>
          <EvidenceList items={group.code} id={`${idBase}-code`} />
        </div>
      ) : null}
      {group.docs.length ? (
        <div className={styles['storyLinkedWork']}>
          <Eyebrow>Documentation</Eyebrow>
          <EvidenceList items={group.docs} id={`${idBase}-docs`} />
        </div>
      ) : null}
    </li>
  );
}

/** The story groups, folding past the first three — same bargain as EvidenceList. */
function StoryGroups({ groups, slug }: { groups: StoryGroup[]; slug: string }) {
  const [open, setOpen] = useState(false);
  const head = groups.slice(0, VISIBLE_ROWS);
  const rest = groups.slice(VISIBLE_ROWS);
  const foldId = `ev-${slug}-stories-more`;
  return (
    <div>
      <ul className={styles['storyList']}>
        {head.map((group, index) => (
          <StorySection key={`${group.story.key}-${index}`} group={group} slug={slug} index={index} />
        ))}
      </ul>
      {rest.length ? (
        <>
          <ul id={foldId} hidden={!open} className={styles['storyList']}>
            {rest.map((group, index) => (
              <StorySection
                key={`${group.story.key}-${index}`}
                group={group}
                slug={slug}
                index={VISIBLE_ROWS + index}
              />
            ))}
          </ul>
          <button
            type="button"
            className={styles['moreToggle']}
            aria-expanded={open}
            aria-controls={foldId}
            onClick={() => setOpen((v) => !v)}
          >
            {open ? 'Show fewer' : `+ ${rest.length} more`}
          </button>
        </>
      ) : null}
    </div>
  );
}

/**
 * The card's board status, derived — the payload has no member status because
 * people are not tickets. Blocked wins outright; otherwise the ticketing
 * evidence decides between done and in-progress; no activity at all is the
 * grey to-do colour with the honest words.
 */
function memberStatus(member: StandupMember): { category: LozengeCategory; word: string } {
  if (member.blockers) return { category: 'blocked', word: 'Blocked' };
  const active = member.counts.some((count) => count > 0);
  if (!active) return { category: 'todo', word: 'No activity' };
  const statuses = (member.categories.find((c) => c.label === 'Ticketing')?.evidence ?? [])
    .map((item) => statusCategory(item.status))
    .filter((category): category is LozengeCategory => category !== null);
  if (statuses.length && statuses.every((category) => category === 'done')) {
    return { category: 'done', word: 'Done' };
  }
  return { category: 'inprogress', word: 'In Progress' };
}

/** Practice rules that are really "work with no ticket behind it" — those get
 * their own Jira-style section instead of the generic practices block. */
const UNTRACKED_RULES = new Set(['untracked-work', 'untracked-docs']);

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

function Member({ member, correctable }: { member: StandupMember; correctable: boolean }) {
  const chips = member.counts
    .map((count, i) => {
      const [singular, plural] = CATEGORY_NOUNS[i] as readonly [string, string];
      return { count, noun: count === 1 ? singular : plural, chipTone: CATEGORY_TONES[i] as Tone };
    })
    .filter(({ count }) => count > 0);
  const slug = memberSlug(member.name);
  const status = memberStatus(member);
  const ticket = topTicket(member);
  const ticketUrl = ticket ? safeUrl(ticket.url) : '';
  // Section order is fixed by this component, whatever order the payload sent.
  // It differs by view on purpose: a storied card leads with Stories (the unit
  // of work), a flat card leads with the work and ends on the ticket feed.
  const byLabel = new Map(member.categories.map((category) => [category.label, category]));
  const code = byLabel.get('Code');
  const docs = byLabel.get('Documentation');
  const ticketing = byLabel.get('Ticketing');
  const other = member.categories.filter((c) => !['Code', 'Documentation', 'Ticketing'].includes(c.label));
  const grouped = groupStories(ticketing?.evidence ?? [], code?.evidence ?? [], docs?.evidence ?? []);
  // Without hierarchy facts (legacy payloads, keyless boards) the flat ticket
  // feed is the honest view — and pixel-identical to what it always rendered.
  const storied = hasHierarchy(grouped);
  const practices = member.practices ?? [];
  const untracked = practices.filter((practice) => UNTRACKED_RULES.has(practice.rule));
  const coached = practices.filter((practice) => !UNTRACKED_RULES.has(practice.rule));

  return (
    <div
      id={`m-${slug}`}
      className={member.blockers ? `${styles['member']} ${styles['blocked']}` : styles['member']}
    >
      {/* The issue-header furniture a Jira user orients by: key left, status
          right. The key is the member's headline ticket — absent rather than
          invented when they touched none. */}
      <div className={styles['issueHead']}>
        {ticket ? (
          ticketUrl ? (
            <a
              className={styles['issueKey']}
              href={ticketUrl}
              // Same origin rule as Chip: the tracker must not learn the tunnel URL.
              target="_blank"
              rel="noopener noreferrer"
            >
              {ticket.key}
            </a>
          ) : (
            <span className={styles['issueKey']}>{ticket.key}</span>
          )
        ) : null}
        <Lozenge category={status.category}>{status.word}</Lozenge>
      </div>
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
        </div>
      </div>

      {/* The blocker leads the card as Jira's flag idiom — the one thing on
          this page somebody has to act on, styled like a flagged issue. */}
      {member.blockers ? (
        <Field edit={member.edit} field="blockers" label={`${member.name}'s blocker`}>
          <p className={styles['impediment']}>
            <span className={styles['impedimentFlag']} aria-hidden="true">
              ⚑
            </span>{' '}
            <strong>Flagged</strong> — impediment: <RichText runs={member.blockers} />
          </p>
        </Field>
      ) : null}

      <CardSection label="Description">
        <Field edit={member.edit} field="summary" label={`${member.name}'s summary`}>
          {member.summary.length ? (
            <ul className={styles['memberSummary']}>
              {member.summary.flatMap(splitTicketBullets).map((runs, index) => (
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
      </CardSection>

      {storied && ticketing ? (
        <>
          {/* Story-centred view: each user story with its subtasks (one line
              each) and the code/doc changes that name it; only work no story
              claimed stays in the Code / Documentation sections below. */}
          <CardSection label="Stories" tone={tone(CATEGORY_TONE_BY_LABEL, 'Ticketing')}>
            {ticketing.items.length ? (
              <ul className={styles['bullets']}>
                {ticketing.items.flatMap(splitTicketBullets).map((runs, index) => (
                  <li key={index}>
                    <RichText runs={runs} />
                  </li>
                ))}
              </ul>
            ) : null}
            <StoryGroups groups={grouped.groups} slug={slug} />
          </CardSection>
          {code ? <Category category={code} slug={slug} evidence={grouped.looseCode} /> : null}
          {docs ? <Category category={docs} slug={slug} evidence={grouped.looseDocs} /> : null}
        </>
      ) : (
        <>
          {code ? <Category category={code} slug={slug} /> : null}
          {docs ? <Category category={docs} slug={slug} /> : null}
          {/* The Ticketing category, in Jira's clothes: the prose says what moved,
              the feed rows put a status lozenge on every ticket. */}
          {ticketing ? (
            <Category category={ticketing} slug={slug} label="Ticket status changes" variant="feed" />
          ) : null}
        </>
      )}
      {other.map((category) => (
        <Category key={category.label} category={category} slug={slug} />
      ))}
      {member.footnotes.map((footnote) => (
        <p key={footnote.label} className={styles['footnote']}>
          {footnote.label} — <RichText runs={footnote.runs} />
        </p>
      ))}

      {/* Work with no ticket behind it gets its own section — after the
          categories, because what shipped is the context that makes "and no
          ticket behind it" mean anything. Voting rides along unchanged. */}
      {untracked.length ? (
        <CardSection label="Untracked work">
          <ul className={styles['practiceList']}>
            {untracked.map((practice) => (
              <Practice key={practice.rule} member={member.name} practice={practice} correctable={correctable} />
            ))}
          </ul>
        </CardSection>
      ) : null}
      {coached.length ? <Practices member={member.name} practices={coached} correctable={correctable} /> : null}

      {member.outlook ? (
        <CardSection label="What's next">
          <Field edit={member.edit} field="outlook" label={`${member.name}'s outlook`}>
            <p className={styles['note']}>
              <RichText runs={member.outlook} />
            </p>
          </Field>
        </CardSection>
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
