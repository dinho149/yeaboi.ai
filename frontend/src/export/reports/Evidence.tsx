/**
 * Structured evidence rows — what replaced the bare SHA chip row.
 *
 * The old `Links` chips rendered `(sha8, url)` pairs with no kind, no title and
 * no repo, so a CODE block ended in `78E4201D 94883E6A …` — labels nobody could
 * read. Each row here says what the item *is* (kind chip, the word beside the
 * colour), which one (mono key, linked when the URL survived the allowlist),
 * what it did (the collector's title), and where (repo · status, muted).
 *
 * Overflow folds behind an `aria-expanded` button after the first three rows —
 * the CarriedStrip bargain: nothing is hidden, only folded, and the visible
 * rows carry the signal. A PR's commits fold the same way, under their PR row,
 * so the visible list stays a list of units of work.
 */

import { useState } from 'react';

import { Chip, Lozenge } from '../../design/primitives';
import type { LozengeCategory } from '../../design/primitives';
import type { Tone } from '../../design/tone';
import { cx } from '../../runtime/cx';
import { safeUrl } from '../../runtime/url';
import type { EvidenceItem } from '../boot';
import styles from './reports.module.css';

/** How an evidence list dresses its rows. */
export type EvidenceVariant = 'default' | 'feed';

/** Rows always visible; the rest fold behind the "+ N more" toggle. */
export const VISIBLE_ROWS = 3;

/** `kind` is engine-produced, not validated — an unknown one degrades to muted. */
const KIND_META: Record<string, { label: string; tone: Tone }> = {
  commit: { label: 'commit', tone: 'accent2' },
  pr: { label: 'PR', tone: 'accent' },
  review: { label: 'review', tone: 'info' },
  comment: { label: 'comment', tone: 'low' },
  issue: { label: 'ticket', tone: 'accent' },
  update: { label: 'ticket', tone: 'accent' },
  work_item: { label: 'ticket', tone: 'accent' },
  ticket: { label: 'ticket', tone: 'accent' },
  wip: { label: 'in progress', tone: 'warn' },
  page: { label: 'doc', tone: 'info' },
  'page-created': { label: 'doc', tone: 'info' },
};

function kindMeta(kind: string): { label: string; tone: Tone } {
  return KIND_META[kind] ?? { label: kind || 'ref', tone: 'low' };
}

/**
 * Board statuses keyword-matched into Jira's status categories, because
 * statuses are the tracker instance's own vocabulary ("In Test", "Doing", …).
 * Jira itself has three categories (to-do, in-progress, done); `blocked` is
 * the extra one a standup needs. The word ALWAYS renders inside the lozenge —
 * house rule: never a colour or symbol alone. Unknown statuses degrade to the
 * grey to-do lozenge with the plain word.
 *
 * One table serves both the per-row lozenge and the member-card status
 * derivation in Standup.tsx, so the two can never disagree.
 */
export const STATUS_RULES: Array<{ match: RegExp; category: LozengeCategory }> = [
  { match: /done|closed|resolved|complete|merged/, category: 'done' },
  { match: /blocked|hold|impeded|stuck|waiting/, category: 'blocked' },
  { match: /review|test|qa/, category: 'inprogress' },
  { match: /progress|doing|active/, category: 'inprogress' },
  { match: /to.?do|new|open|backlog/, category: 'todo' },
];

/** Jira category for a tracker's status word; null when nothing matches. */
export function statusCategory(status: string): LozengeCategory | null {
  const lowered = status.toLowerCase();
  return STATUS_RULES.find((s) => s.match.test(lowered))?.category ?? null;
}

export function EvidenceRow({ item, idBase, variant = 'default' }: { item: EvidenceItem; idBase: string; variant?: EvidenceVariant }) {
  const [open, setOpen] = useState(false);
  const meta = kindMeta(item.kind);
  const url = safeUrl(item.url);
  const children = item.children ?? [];
  // Doc keys are machine ids (a Confluence page id, a Notion UUID) — the title
  // is the human handle, so it becomes the link text and the id is dropped.
  const isDoc = meta.label === 'doc';
  // When there is no key the title stands in for it, and is not repeated.
  const key = isDoc ? item.title || item.key || 'ref' : item.key || item.title || 'ref';
  const title = isDoc || !item.key ? '' : item.title;
  // A wip row's kind chip already says "in progress" — repeating the status
  // under it is noise, and noise is what this component replaced. The feed
  // variant has no kind chip, so its status always shows.
  const status = variant === 'default' && item.status.toLowerCase() === meta.label.toLowerCase() ? '' : item.status;
  const category = status ? statusCategory(status) : null;
  const childrenId = `${idBase}-commits`;
  // In the ticket-status feed the row IS a ticket — a "ticket" chip on every
  // line is furniture, and the lozenge moves to the end like a Jira board row.
  const feed = variant === 'feed';
  // The tracker's own type word ("Story", "Sub-task") — feed rows only, where
  // the kind chip that would have carried a word is gone.
  const typeWord = feed ? (item.type ?? '') : '';

  const lozenge = status ? (
    <Lozenge small category={category ?? 'todo'} className={styles['evidenceStatus']}>
      {status}
    </Lozenge>
  ) : null;

  return (
    <li className={cx(styles['evidenceRow'], feed && styles['evidenceFeedRow'])}>
      {feed ? null : <Chip tone={meta.tone}>{meta.label}</Chip>}
      {url ? (
        // Same origin rule as Chip: the tracker must not learn the tunnel URL.
        <a className={styles['evidenceRef']} href={url} target="_blank" rel="noopener noreferrer">
          {key}
        </a>
      ) : (
        <span className={styles['evidenceRef']}>{key}</span>
      )}
      {title ? (
        <span className={styles['evidenceTitle']} title={title}>
          {title}
        </span>
      ) : null}
      {typeWord || item.repo || status || children.length ? (
        <span className={styles['evidenceMeta']}>
          {typeWord}
          {typeWord && (item.repo || status) ? ' · ' : ''}
          {item.repo}
          {item.repo && status ? ' · ' : ''}
          {lozenge}
          {children.length ? (
            <>
              {item.repo || status ? ' · ' : ''}
              <button
                type="button"
                className={styles['commitToggle']}
                aria-expanded={open}
                aria-controls={childrenId}
                onClick={() => setOpen((v) => !v)}
              >
                {open ? '▾' : '▸'} {children.length} commit{children.length === 1 ? '' : 's'}
              </button>
            </>
          ) : null}
        </span>
      ) : null}
      {children.length ? (
        <ul id={childrenId} hidden={!open} className={styles['evidenceChildren']}>
          {children.map((child, index) => (
            <EvidenceRow key={`${child.kind}-${child.key}-${index}`} item={child} idBase={`${childrenId}-${index}`} />
          ))}
        </ul>
      ) : null}
    </li>
  );
}

export function EvidenceList({ items, id, variant = 'default' }: { items: EvidenceItem[]; id: string; variant?: EvidenceVariant }) {
  const [open, setOpen] = useState(false);
  if (!items.length) return null;
  const head = items.slice(0, VISIBLE_ROWS);
  const rest = items.slice(VISIBLE_ROWS);

  return (
    <div>
      <ul className={styles['evidence']}>
        {head.map((item, index) => (
          <EvidenceRow key={`${item.kind}-${item.key}-${index}`} item={item} idBase={`${id}-h${index}`} variant={variant} />
        ))}
      </ul>
      {rest.length ? (
        <>
          <ul id={id} hidden={!open} className={styles['evidence']}>
            {rest.map((item, index) => (
              <EvidenceRow key={`${item.kind}-${item.key}-${index}`} item={item} idBase={`${id}-r${index}`} variant={variant} />
            ))}
          </ul>
          <button
            type="button"
            className={styles['moreToggle']}
            aria-expanded={open}
            aria-controls={id}
            onClick={() => setOpen((v) => !v)}
          >
            {open ? 'Show fewer' : `+ ${rest.length} more`}
          </button>
        </>
      ) : null}
    </div>
  );
}
