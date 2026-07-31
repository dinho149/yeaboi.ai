/**
 * Reading Markdown into blocks.
 *
 * The anonymize export is the one report whose *input* is a Markdown string —
 * it takes another mode's already-masked document and re-publishes it — so
 * something has to read Markdown somewhere. Until now that was
 * `anonymize.export._md_to_html`, 110 lines of Python that escaped each line
 * and then spliced HTML back into it.
 *
 * Two reasons it moved here rather than being ported field-for-field:
 *
 * * **Nothing produces a string of markup any more.** The output is a block
 *   tree of plain text; React writes the elements. `<script>` in a masked
 *   document is a five-character text node, not a thing that has to be escaped
 *   correctly on every one of a dozen code paths.
 * * **The payload gets smaller and more honest.** The document travels as the
 *   Markdown it already is, so the exported HTML and the sibling `.md` file are
 *   provably the same text rather than two renderings that could drift.
 *
 * The grammar is deliberately the same narrow one the Python had — the
 * constructs *our own* `build_*_markdown` builders emit. Anything unrecognised
 * degrades to a paragraph, which is the correct failure: readable, inert, and
 * obviously not styled.
 */

import type { Run } from '../design/primitives';

export type MdBlock =
  | { t: 'h'; level: number; runs: Run[] }
  | { t: 'p'; runs: Run[] }
  | { t: 'quote'; runs: Run[] }
  | { t: 'code'; text: string }
  | { t: 'list'; ordered: boolean; items: Run[][] }
  | { t: 'rule' }
  | { t: 'table'; head: Run[][]; rows: Run[][][] };

/**
 * Inline constructs, in precedence order.
 *
 * Order is the whole reason this is one alternation rather than four passes.
 * A regex engine tries branches left to right at each position, so ``code``
 * wins over the `*`s inside it and `**bold**` wins over `*italic*` — which is
 * exactly what the four sequential `re.sub` calls in the Python were arranging
 * by running in that order, minus the part where each pass had to avoid
 * re-matching the markup the previous one produced.
 */
const INLINE_RE = /`([^`]+)`|\*\*([^*]+)\*\*|\*([^*]+)\*(?!\*)|\[([^\]]+)\]\(([^)]+)\)/g;

/** Parse one line of inline Markdown into runs. Never returns an empty array for non-empty input. */
export function inlineRuns(text: string): Run[] {
  const runs: Run[] = [];
  let at = 0;

  INLINE_RE.lastIndex = 0;
  for (let m = INLINE_RE.exec(text); m !== null; m = INLINE_RE.exec(text)) {
    if (m.index > at) runs.push({ s: text.slice(at, m.index) });
    const [, code, strong, em, label, href] = m;
    if (code !== undefined) runs.push({ s: code, code: true });
    else if (strong !== undefined) runs.push({ s: strong, strong: true });
    else if (em !== undefined) runs.push({ s: em, em: true });
    else if (label !== undefined) runs.push({ s: label, href: href ?? '' });
    at = m.index + m[0].length;
  }
  if (at < text.length) runs.push({ s: text.slice(at) });
  return runs;
}

const HEADING_RE = /^(#{1,6})\s+(.*)$/;
/** A whole-line image: `![Delivered items](delivered.png)`. See the note in readMarkdown. */
const IMAGE_LINE_RE = /^!\[([^\]]*)\]\(([^)]+)\)$/;
const RULE_RE = /^(-{3,}|\*{3,}|_{3,})$/;
const BULLET_RE = /^[-*+]\s+(.*)$/;
const NUMBERED_RE = /^\d+[.)]\s+(.*)$/;
/** A pipe-table divider: `|---|:--:|`. The `-` is checked separately. */
const DIVIDER_RE = /^\s*\|?[\s:|-]+\|?\s*$/;

function cells(row: string): Run[][] {
  return row
    .trim()
    .replace(/^\||\|$/g, '')
    .split('|')
    .map((cell) => inlineRuns(cell.trim()));
}

/** Read a Markdown document into blocks. */
export function readMarkdown(md: string): MdBlock[] {
  const lines = (md ?? '').split('\n');
  const out: MdBlock[] = [];
  let i = 0;

  while (i < lines.length) {
    const raw = lines[i] ?? '';
    const line = raw.trim();

    if (!line) {
      i += 1;
      continue;
    }

    if (line.startsWith('```')) {
      i += 1;
      const code: string[] = [];
      while (i < lines.length && !(lines[i] ?? '').trim().startsWith('```')) {
        code.push(lines[i] ?? '');
        i += 1;
      }
      i += 1; // the closing fence, or past the end for an unterminated block
      out.push({ t: 'code', text: code.join('\n') });
      continue;
    }

    if (RULE_RE.test(line)) {
      out.push({ t: 'rule' });
      i += 1;
      continue;
    }

    // An image line degrades to its alt text, italicised — the same answer
    // `markdown_convert` gives an unmapped image. This matters because the
    // anonymize renderer is what every mode's *masked share* goes through, and
    // a reporting export carries `![Delivered items](delivered.png)`: a chart
    // written beside the .md, which a shared page does not have. Without this,
    // the inline link rule matches the `[…](…)` half and emits a stray `!`
    // followed by a link to a file that is not there.
    const image = IMAGE_LINE_RE.exec(line);
    if (image) {
      out.push({ t: 'p', runs: [{ s: image[1] || 'image', em: true }] });
      i += 1;
      continue;
    }

    const heading = HEADING_RE.exec(line);
    if (heading) {
      out.push({ t: 'h', level: (heading[1] ?? '#').length, runs: inlineRuns((heading[2] ?? '').trim()) });
      i += 1;
      continue;
    }

    // A pipe table is a header row *followed by* a divider — the lookahead is
    // what stops a prose line containing a pipe from becoming a one-cell table.
    const next = lines[i + 1];
    if (line.includes('|') && next !== undefined && DIVIDER_RE.test(next.trim()) && next.includes('-')) {
      const head = cells(line);
      i += 2;
      const rows: Run[][][] = [];
      while (i < lines.length && (lines[i] ?? '').includes('|') && (lines[i] ?? '').trim()) {
        rows.push(cells(lines[i] ?? ''));
        i += 1;
      }
      out.push({ t: 'table', head, rows });
      continue;
    }

    if (line.startsWith('>')) {
      out.push({ t: 'quote', runs: inlineRuns(line.replace(/^[>\s]+/, '')) });
      i += 1;
      continue;
    }

    const bullet = BULLET_RE.exec(line);
    const numbered = bullet ? null : NUMBERED_RE.exec(line);
    if (bullet || numbered) {
      const ordered = numbered !== null;
      const items: Run[][] = [];
      // Consume the whole run of same-kind items, so switching from `-` to `1.`
      // starts a second list rather than silently continuing the first.
      while (i < lines.length) {
        const item = (lines[i] ?? '').trim();
        const match = ordered ? NUMBERED_RE.exec(item) : BULLET_RE.exec(item);
        if (!match) break;
        items.push(inlineRuns(match[1] ?? ''));
        i += 1;
      }
      out.push({ t: 'list', ordered, items });
      continue;
    }

    out.push({ t: 'p', runs: inlineRuns(line) });
    i += 1;
  }

  return out;
}
