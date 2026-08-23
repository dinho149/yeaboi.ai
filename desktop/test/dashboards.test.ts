// The dashboards' pure half: how a run's NDJSON lines fold into state, and the
// weekday spec the schedule form sends. The wire itself is pinned on the Python
// side (test_app_wire.py + contracts/v1/app_http.md); this is the renderer's
// reading of it.

import { describe, expect, it } from 'vitest';
import { type RunLine, emptyRun, reduceRun, weekdaySpec } from '../src/renderer/dashboards';

function fold(lines: RunLine[]) {
  return lines.reduce(reduceRun, emptyRun());
}

describe('reduceRun', () => {
  it('keeps the op id so an analysis run stays cancellable', () => {
    expect(fold([{ type: 'op', op_id: 'op-1' }]).opId).toBe('op-1');
  });

  it('accumulates phases in the order they were reported', () => {
    const run = fold([
      { type: 'op', op_id: 'x' },
      { type: 'progress', phase: 'Collecting activity' },
      { type: 'progress', phase: 'Writing the summary' },
    ]);
    expect(run.phases).toEqual(['Collecting activity', 'Writing the summary']);
    expect(run.finished).toBe(false);
  });

  it('carries the standup run id so the page can point back at this run', () => {
    expect(fold([{ type: 'run_id', run_id: 7 }]).runId).toBe(7);
  });

  it('finishes with the report a standup run produced', () => {
    const report = { team_summary: 'steady progress' } as never;
    const run = fold([{ type: 'done', report }]);
    expect(run.report).toBe(report);
    expect(run.result).toBeNull();
    expect(run.finished).toBe(true);
  });

  it('finishes with the result an analysis run produced', () => {
    const run = fold([{ type: 'done', result: { delivery: {} } }]);
    expect(run.result).toEqual({ delivery: {} });
    expect(run.report).toBeNull();
  });

  it('reports a cancelled run as finished without an error', () => {
    const run = fold([{ type: 'op', op_id: 'x' }, { type: 'cancelled' }]);
    expect(run.cancelled).toBe(true);
    expect(run.error).toBe('');
    expect(run.finished).toBe(true);
  });

  it('carries a classified error line through', () => {
    const run = fold([{ type: 'error', message: 'Rate limited — wait a moment.' }]);
    expect(run.error).toBe('Rate limited — wait a moment.');
    expect(run.finished).toBe(true);
  });

  it('ignores a line type it does not know', () => {
    // A newer backend is not a failure — an unknown type must not blank the run.
    const run = fold([{ type: 'progress', phase: 'a' }, { type: 'weather' } as unknown as RunLine]);
    expect(run.phases).toEqual(['a']);
  });

  it('never mutates the state it was handed', () => {
    const before = emptyRun();
    reduceRun(before, { type: 'progress', phase: 'a' });
    expect(before.phases).toEqual([]);
  });
});

describe('weekdaySpec', () => {
  it('collapses a consecutive run into a range', () => {
    expect(weekdaySpec([1, 2, 3, 4, 5])).toBe('1-5');
  });

  it('keeps separate days separate', () => {
    expect(weekdaySpec([1, 3, 5])).toBe('1,3,5');
  });

  it('mixes ranges and single days', () => {
    expect(weekdaySpec([1, 2, 3, 6])).toBe('1-3,6');
  });

  it('sorts and de-duplicates whatever the picker produced', () => {
    expect(weekdaySpec([5, 1, 2, 1])).toBe('1-2,5');
  });

  it('falls back to the working week rather than sending nothing', () => {
    // An empty spec would install a job that never fires.
    expect(weekdaySpec([])).toBe('1-5');
  });
});
