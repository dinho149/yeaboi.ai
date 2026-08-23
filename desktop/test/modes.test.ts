// The pure halves of the reporting/performance/roadmap/ship wire: the run
// reducer and the two hash readers. Everything else in modes.ts is a call,
// which the route tests cover on the Python side.

import { describe, expect, it } from 'vitest';
import { emptyModeRun, numberFromHash, reduceModeRun, shipKeyFromHash } from '../src/renderer/modes';

describe('reduceModeRun', () => {
  const fold = (lines: unknown[]) => lines.reduce(reduceModeRun, emptyModeRun());

  it('starts empty and unfinished', () => {
    expect(emptyModeRun()).toEqual({
      opId: '',
      phases: [],
      done: null,
      error: '',
      cancelled: false,
      finished: false,
    });
  });

  it('records the op so the run can be stopped', () => {
    expect(fold([{ type: 'op', op_id: 'abc' }]).opId).toBe('abc');
  });

  it('accumulates progress in order', () => {
    const state = fold([
      { type: 'progress', phase: 'Gathering delivered work' },
      { type: 'progress', phase: 'Writing the narrative' },
    ]);
    expect(state.phases).toEqual(['Gathering delivered work', 'Writing the narrative']);
    expect(state.finished).toBe(false);
  });

  it('keeps the whole done line, whatever the run yields', () => {
    // Reporting sends a report, roadmap sends an analysis — the reducer is
    // the same because the line vocabulary is.
    const report = fold([{ type: 'done', report: { headline: 'Billing shipped' }, delivered: 12 }]);
    expect(report.done?.delivered).toBe(12);
    const roadmap = fold([{ type: 'done', analysis: { projects: [] }, roadmap_id: 7 }]);
    expect(roadmap.done?.roadmap_id).toBe(7);
    expect(roadmap.finished).toBe(true);
  });

  it('treats a cancellation as finished, not failed', () => {
    const state = fold([{ type: 'op', op_id: 'a' }, { type: 'cancelled' }]);
    expect(state).toMatchObject({ cancelled: true, finished: true, error: '' });
  });

  it('carries the error message and stops', () => {
    const state = fold([{ type: 'error', message: 'Jira refused the request' }]);
    expect(state.error).toBe('Jira refused the request');
    expect(state.finished).toBe(true);
  });

  it('falls back to a plain sentence when an error line carries none', () => {
    expect(fold([{ type: 'error' }]).error).toBe('The run stopped.');
  });

  it('ignores an unknown line type — a newer backend is not a failure', () => {
    const state = fold([{ type: 'op', op_id: 'a' }, { type: 'telemetry', n: 3 }]);
    expect(state).toMatchObject({ opId: 'a', finished: false });
  });

  it('survives a malformed line', () => {
    expect(fold([null, undefined, 'nonsense'])).toEqual(emptyModeRun());
  });
});

describe('shipKeyFromHash', () => {
  it('reads the run key', () => {
    expect(shipKeyFromHash('#/humans/ship/run?key=abc123')).toBe('abc123');
  });

  it('is empty when the hash carries none', () => {
    expect(shipKeyFromHash('#/humans/ship')).toBe('');
    expect(shipKeyFromHash('')).toBe('');
  });

  it('decodes an escaped key', () => {
    expect(shipKeyFromHash('#/humans/ship/run?key=a%2Fb')).toBe('a/b');
  });
});

describe('numberFromHash', () => {
  it('reads a positive id', () => {
    expect(numberFromHash('#/humans/reporting?run_id=42', 'run_id')).toBe(42);
  });

  it('is zero for anything that is not a positive number', () => {
    // Zero is the "no id" value every caller already means by it, so a
    // missing, negative or junk value must not become a lookup.
    expect(numberFromHash('#/humans/reporting', 'run_id')).toBe(0);
    expect(numberFromHash('#/humans/reporting?run_id=-3', 'run_id')).toBe(0);
    expect(numberFromHash('#/humans/reporting?run_id=abc', 'run_id')).toBe(0);
  });
});
