/**
 * Reading Markdown into blocks.
 *
 * These are the cases the Python renderer this replaced actually had to handle
 * — the constructs our own `build_*_markdown` builders emit — plus the ones
 * that made the old design fragile: a line that *looks* like a table, emphasis
 * markers nested inside code, and a link whose scheme executes.
 */

import { describe, expect, it } from 'vitest';

import { inlineRuns, readMarkdown } from './markdown';

describe('inlineRuns', () => {
  it('reads plain text as one run', () => {
    expect(inlineRuns('just words')).toEqual([{ s: 'just words' }]);
  });

  it('reads bold, italic, code and links', () => {
    expect(inlineRuns('a **b** c *d* e `f` g [h](https://x.test)')).toEqual([
      { s: 'a ' },
      { s: 'b', strong: true },
      { s: ' c ' },
      { s: 'd', em: true },
      { s: ' e ' },
      { s: 'f', code: true },
      { s: ' g ' },
      { s: 'h', href: 'https://x.test' },
    ]);
  });

  it('leaves emphasis markers inside code alone', () => {
    // The reason code is first in the alternation. Four sequential regex
    // passes had to arrange this by ordering *and* by not re-matching the
    // markup the previous pass emitted.
    expect(inlineRuns('`a **b** c`')).toEqual([{ s: 'a **b** c', code: true }]);
  });

  it('does not mistake bold for two italics', () => {
    expect(inlineRuns('**loud**')).toEqual([{ s: 'loud', strong: true }]);
  });

  it('keeps a dangerous scheme as data — the allowlist is RichText’s job', () => {
    // Parsing and rendering are separate concerns and only one of them can see
    // an `href`. Dropping it here would also silently eat the link's *text*,
    // which is the half a reader still needs.
    const [run] = inlineRuns('See [the ticket](javascript:alert(1)) for details.');
    expect(run).toMatchObject({ s: 'See ' });
    expect(inlineRuns('[x](javascript:alert1)')).toEqual([{ s: 'x', href: 'javascript:alert1' }]);
  });
});

describe('readMarkdown', () => {
  it('reads headings at their level', () => {
    expect(readMarkdown('# One\n\n### Three')).toEqual([
      { t: 'h', level: 1, runs: [{ s: 'One' }] },
      { t: 'h', level: 3, runs: [{ s: 'Three' }] },
    ]);
  });

  it('groups a run of bullets into one list', () => {
    const [list] = readMarkdown('- a\n- b\n- c');
    expect(list).toEqual({ t: 'list', ordered: false, items: [[{ s: 'a' }], [{ s: 'b' }], [{ s: 'c' }]] });
  });

  it('starts a second list when the marker kind changes', () => {
    const blocks = readMarkdown('- a\n1. b');
    expect(blocks.map((b) => b.t)).toEqual(['list', 'list']);
    expect(blocks[1]).toMatchObject({ ordered: true });
  });

  it('reads a fenced block verbatim, markers and all', () => {
    expect(readMarkdown('```\nconst a = **1**;\n  indented\n```')).toEqual([
      { t: 'code', text: 'const a = **1**;\n  indented' },
    ]);
  });

  it('closes an unterminated fence at the end of the document', () => {
    // A truncated export is a real input: the anonymize engine caps its source.
    expect(readMarkdown('```\nstill going')).toEqual([{ t: 'code', text: 'still going' }]);
  });

  it('reads a pipe table', () => {
    const [table] = readMarkdown('| A | B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |');
    expect(table).toEqual({
      t: 'table',
      head: [[{ s: 'A' }], [{ s: 'B' }]],
      rows: [
        [[{ s: '1' }], [{ s: '2' }]],
        [[{ s: '3' }], [{ s: '4' }]],
      ],
    });
  });

  it('does not turn a sentence containing a pipe into a table', () => {
    // The divider lookahead is the whole guard. Without it "a | b" alone reads
    // as a one-row table and the prose disappears into a grid.
    expect(readMarkdown('run a | b to compare')).toEqual([{ t: 'p', runs: [{ s: 'run a | b to compare' }] }]);
  });

  it('reads rules, quotes and paragraphs', () => {
    expect(readMarkdown('> noted\n\n---\n\nplain')).toEqual([
      { t: 'quote', runs: [{ s: 'noted' }] },
      { t: 'rule' },
      { t: 'p', runs: [{ s: 'plain' }] },
    ]);
  });

  it('degrades an unrecognised line to a paragraph rather than dropping it', () => {
    expect(readMarkdown('<div onclick="alert(1)">hi</div>')).toEqual([
      { t: 'p', runs: [{ s: '<div onclick="alert(1)">hi</div>' }] },
    ]);
  });

  it('degrades an image line to its alt text', () => {
    // Every mode's *masked share* renders through this reader, and a masked
    // reporting export carries a chart image whose file the share does not
    // have. A stray "!" followed by a dead link is worse than the caption.
    expect(readMarkdown('![Delivered items](delivered.png)')).toEqual([
      { t: 'p', runs: [{ s: 'Delivered items', em: true }] },
    ]);
    expect(readMarkdown('![](chart.png)')).toEqual([{ t: 'p', runs: [{ s: 'image', em: true }] }]);
  });

  it('reads nothing from nothing', () => {
    expect(readMarkdown('')).toEqual([]);
    expect(readMarkdown('\n\n  \n')).toEqual([]);
  });
});
