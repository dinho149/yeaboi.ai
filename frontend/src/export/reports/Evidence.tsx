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

import { Chip } from '../../design/primitives';
import { toneVar } from '../../design/tone';
import type { Tone } from '../../design/tone';
import { safeUrl } from '../../runtime/url';
import type { EvidenceItem } from '../boot';
import styles from './reports.module.css';

/** Rows always visible; the rest fold behind the "+ N more" toggle. */
const VISIBLE_ROWS = 3;

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
 * A glyph for the board status, keyword-matched because statuses are the Jira
 * instance's own vocabulary ("In Test", "Doing", …). The word ALWAYS renders
 * beside it — house rule: never a colour or symbol alone. Unknown statuses
 * degrade to the plain muted word.
 */
const STATUS_GLYPHS: Array<{ match: RegExp; glyph: string; tone: Tone }> = [
  { match: /done|closed|resolved|complete/, glyph: '✓', tone: 'ok' },
  { match: /blocked|hold|impeded|stuck|waiting/, glyph: '⛔', tone: 'danger' },
  { match: /review|test|qa/, glyph: '→', tone: 'info' },
  { match: /progress|doing|active/, glyph: '◐', tone: 'warn' },
  { match: /to.?do|new|open|backlog/, glyph: '○', tone: 'low' },
];

function statusGlyph(status: string): { glyph: string; tone: Tone } | null {
  const lowered = status.toLowerCase();
  return STATUS_GLYPHS.find((s) => s.match.test(lowered)) ?? null;
}

function EvidenceRow({ item, idBase }: { item: EvidenceItem; idBase: string }) {
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
  // under it is noise, and noise is what this component replaced.
  const status = item.status.toLowerCase() === meta.label.toLowerCase() ? '' : item.status;
  const glyph = status ? statusGlyph(status) : null;
  const childrenId = `${idBase}-commits`;

  return (
    <li className={styles['evidenceRow']}>
      <Chip tone={meta.tone}>{meta.label}</Chip>
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
      {item.repo || status || children.length ? (
        <span className={styles['evidenceMeta']}>
          {item.repo}
          {item.repo && status ? ' · ' : ''}
          {status ? (
            <span className={styles['evidenceStatus']} style={glyph ? { color: toneVar(glyph.tone) } : undefined}>
              {glyph ? `${glyph.glyph} ` : ''}
              {status}
            </span>
          ) : null}
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

export function EvidenceList({ items, id }: { items: EvidenceItem[]; id: string }) {
  const [open, setOpen] = useState(false);
  if (!items.length) return null;
  const head = items.slice(0, VISIBLE_ROWS);
  const rest = items.slice(VISIBLE_ROWS);

  return (
    <div>
      <ul className={styles['evidence']}>
        {head.map((item, index) => (
          <EvidenceRow key={`${item.kind}-${item.key}-${index}`} item={item} idBase={`${id}-h${index}`} />
        ))}
      </ul>
      {rest.length ? (
        <>
          <ul id={id} hidden={!open} className={styles['evidence']}>
            {rest.map((item, index) => (
              <EvidenceRow key={`${item.kind}-${item.key}-${index}`} item={item} idBase={`${id}-r${index}`} />
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
