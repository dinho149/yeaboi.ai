// The pure halves of the ceremonies/Slack/agents/provenance wire: the agents
// run reducer and the hash reader. Everything else in ops.ts is a call, which
// the route tests cover on the Python side.

import { describe, expect, it } from 'vitest';
import { agentKindFromHash, emptyAgentRun, reduceAgentRun } from '../src/renderer/ops';

describe('reduceAgentRun', () => {
  const fold = (lines: unknown[]) => lines.reduce(reduceAgentRun, emptyAgentRun());

  it('starts empty and unfinished', () => {
    expect(emptyAgentRun()).toEqual({ components: [], phases: [], report: null, error: '', finished: false });
  });

  it('accumulates bare progress strings in order', () => {
    const state = fold([
      { type: 'progress', phase: 'Reading session logs' },
      { type: 'progress', phase: 'Pricing' },
    ]);
    expect(state.phases).toEqual(['Reading session logs', 'Pricing']);
    expect(state.finished).toBe(false);
  });

  it('keeps the latest event per phase, in first-seen order', () => {
    // A scan emits an event per file; appending them all would draw the same
    // phase hundreds of times.
    const state = fold([
      { type: 'component', component: { component_id: 'scan', label: 'Scanning', status: 'running', current: 1 } },
      { type: 'component', component: { component_id: 'price', label: 'Pricing', status: 'running' } },
      { type: 'component', component: { component_id: 'scan', label: 'Scanning', status: 'completed', current: 500 } },
    ]);
    expect(state.components.map((c) => c.component_id)).toEqual(['scan', 'price']);
    expect(state.components[0]?.status).toBe('completed');
    expect(state.components[0]?.current).toBe(500);
  });

  it('finishes on the report', () => {
    const state = fold([{ type: 'done', kind: 'usage', report: { total_cost_usd: 9.99 } }]);
    expect(state.report).toEqual({ total_cost_usd: 9.99 });
    expect(state.finished).toBe(true);
  });

  it('finishes on an error, with a message to show', () => {
    const state = fold([{ type: 'error', message: 'The usage pass stopped unexpectedly — see logs.' }]);
    expect(state.error).toContain('stopped unexpectedly');
    expect(state.finished).toBe(true);
  });

  it('ignores a line type it does not know', () => {
    // A newer backend is not a failure.
    expect(fold([{ type: 'telemetry', value: 1 }])).toEqual(emptyAgentRun());
  });
});

describe('agentKindFromHash', () => {
  it('reads the mode out of an agents route', () => {
    expect(agentKindFromHash('#/agents/advisor')).toBe('advisor');
  });

  it('ignores a query string', () => {
    expect(agentKindFromHash('#/agents/security?from=home')).toBe('security');
  });

  it('is empty off the agents routes', () => {
    expect(agentKindFromHash('#/humans/standup')).toBe('');
    expect(agentKindFromHash('')).toBe('');
  });
});
