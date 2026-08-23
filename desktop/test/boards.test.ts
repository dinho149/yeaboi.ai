// The pure halves of the boards/sharing wire: the anonymize reducer and the
// mask it produces. Everything else in boards.ts is a call, which the route
// tests cover on the Python side.

import { describe, expect, it } from 'vitest';
import { type AnonLine, emptyAnon, maskText, reduceAnon } from '../src/renderer/boards';

describe('reduceAnon', () => {
  const fold = (lines: AnonLine[]) => lines.reduce(reduceAnon, emptyAnon());

  it('starts empty and unfinished', () => {
    expect(emptyAnon()).toEqual({
      opId: '',
      phases: [],
      note: '',
      replacements: [],
      warnings: [],
      error: '',
      finished: false,
    });
  });

  it('records the op so the pass can be cancelled', () => {
    expect(fold([{ type: 'op', op_id: 'abc' }]).opId).toBe('abc');
  });

  it('accumulates progress in order', () => {
    const state = fold([
      { type: 'progress', phase: 'Masking known terms' },
      { type: 'progress', phase: 'Generalising' },
    ]);
    expect(state.phases).toEqual(['Masking known terms', 'Generalising']);
    expect(state.finished).toBe(false);
  });

  it('done carries the map, the note and the warnings', () => {
    const state = fold([
      { type: 'op', op_id: 'abc' },
      { type: 'done', note: '2 masked', replacements: [['Acme', 'Company A']], warnings: ['no model'] },
    ]);
    expect(state.replacements).toEqual([['Acme', 'Company A']]);
    expect(state.note).toBe('2 masked');
    expect(state.warnings).toEqual(['no model']);
    expect(state.finished).toBe(true);
  });

  it('an error finishes the pass', () => {
    const state = fold([{ type: 'error', message: 'Anonymize failed (see logs).' }]);
    expect(state.error).toBe('Anonymize failed (see logs).');
    expect(state.finished).toBe(true);
  });

  it('ignores a line type it does not know', () => {
    // A newer backend, not a failure.
    const state = fold([{ type: 'duck' } as unknown as AnonLine]);
    expect(state).toEqual(emptyAnon());
  });
});

describe('maskText', () => {
  it('returns the text unchanged with no replacements', () => {
    expect(maskText('Ada shipped the login page', [])).toBe('Ada shipped the login page');
  });

  it('handles empty text', () => {
    expect(maskText('', [['Ada', 'Engineer 1']])).toBe('');
  });

  it('replaces every occurrence', () => {
    expect(maskText('Ada asked Ada', [['Ada', 'Engineer 1']])).toBe('Engineer 1 asked Engineer 1');
  });

  it('masks the longest original first', () => {
    // "Acme Payments" must not be half-masked into "Company A Payments".
    const masked = maskText('Acme Payments and Acme', [
      ['Acme', 'Company A'],
      ['Acme Payments', 'Product B'],
    ]);
    expect(masked).toBe('Product B and Company A');
  });

  it('skips an empty original rather than exploding the string', () => {
    expect(maskText('Ada', [['', 'X']])).toBe('Ada');
  });

  it('does not mutate the caller\'s replacement list', () => {
    const replacements: [string, string][] = [
      ['Acme', 'Company A'],
      ['Acme Payments', 'Product B'],
    ];
    maskText('Acme', replacements);
    expect(replacements[0]).toEqual(['Acme', 'Company A']);
  });
});
