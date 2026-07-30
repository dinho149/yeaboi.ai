/**
 * Rendering untrusted text — LLM summaries, ticket descriptions, card bodies.
 *
 * **This is the component that makes `dangerouslySetInnerHTML` unnecessary.**
 * There are exactly three places anyone is tempted to reach for it, and this
 * covers two of them:
 *
 * * Ticket descriptions. They already arrive as plain text (`tickets.py`
 *   strips their HTML), so `white-space: pre-wrap` preserves the line breaks
 *   that were the only reason to want markup.
 * * LLM summaries. {@link proseBullets} splits them into scannable fragments —
 *   the Python `html_theme.prose_bullets`, ported — and each fragment is a
 *   `<li>` with a text child, never a parsed string.
 *
 * (The third is standup's `_linkify`, which injects anchors into
 * already-escaped text. Its replacement is {@link RichText} below.)
 */

import { cx } from '../../runtime/cx';
import { safeUrl } from '../../runtime/url';
import styles from './primitives.module.css';

/** Split on sentence ends. The `[A-Z]` lookahead avoids splitting "e.g. foo". */
const SENTENCE_RE = /(?<=[.!?])\s+(?=[A-Z])/;

/** Split prose into sentences, dropping empties. Mirrors `html_theme.split_sentences`. */
export function splitSentences(text: string): string[] {
  return text
    .trim()
    .split(SENTENCE_RE)
    .map((s) => s.trim())
    .filter(Boolean);
}

/**
 * Split prose into bullet-sized fragments: sentences, then "; " clauses.
 *
 * LLM summaries pack several separate facts into one long sentence, which
 * renders as a wall of text when shown as a single bullet.
 */
export function proseBullets(text: string): string[] {
  const out: string[] = [];
  for (const sentence of splitSentences(text)) {
    for (const part of sentence.split('; ')) {
      const trimmed = part.replace(/^[\s;]+|[\s;]+$/g, '');
      if (trimmed) out.push(trimmed);
    }
  }
  return out;
}

/** A block of untrusted plain text. Line breaks preserved, markup impossible. */
export function Prose({ text, className }: { text: string; className?: string }) {
  return <div className={cx(styles['prose'], styles['proseText'], className)}>{text}</div>;
}

/** An untrusted paragraph split into scannable bullets. */
export function ProseBullets({ text, className }: { text: string; className?: string }) {
  const bullets = proseBullets(text);
  if (!bullets.length) return null;
  return (
    <ul className={cx(styles['prose'], styles['proseList'], className)}>
      {bullets.map((bullet, index) => (
        <li key={`${index}-${bullet.slice(0, 24)}`}>{bullet}</li>
      ))}
    </ul>
  );
}

/**
 * One span of rich text: a string, optionally a link, optionally emphasised.
 *
 * This is the replacement for the escape-then-regex-substitute pattern in
 * `standup._linkify`, which escapes text and then splices raw `<a>` markup back
 * into it — a design that is one regex bug away from an injection, and that has
 * to be re-implemented for the Markdown builder anyway. Producing a `Run[]`
 * lets both renderers consume the same structure, and neither one ever parses
 * a string as HTML.
 */
export interface Run {
  s: string;
  href?: string;
  strong?: boolean;
}

export function RichText({ runs, className }: { runs: readonly Run[]; className?: string }) {
  return (
    <span className={className}>
      {runs.map((run, index) => {
        const key = `${index}-${run.s.slice(0, 16)}`;
        const safe = run.href ? safeUrl(run.href) : '';
        const body = run.strong ? <strong>{run.s}</strong> : run.s;
        if (!safe) return <span key={key}>{body}</span>;
        return (
          <a key={key} href={safe} target="_blank" rel="noopener noreferrer">
            {body}
          </a>
        );
      })}
    </span>
  );
}
