// The plan page's reading of what came back — most of all what a sync did,
// because that one made tickets on somebody's board.

import { describe, expect, it } from 'vitest';
import type { Envelope } from '../src/renderer/api';
import { type Plan, isEmptyPlan, outcomeMessage, planCounts, storiesOf } from '../src/renderer/plan';

const PLAN: Plan = {
  session_id: 'abc',
  project: { name: 'Barber booking' },
  features: [{ id: 'E1' }],
  stories: [
    { id: 'S1', title: 'Book a slot', story_points: 3 },
    { id: 'S2', title: 'Cancel a slot', story_points: 2 },
  ],
  tasks: [{ id: 'T1' }, { id: 'T2' }, { id: 'T3' }],
  sprints: [{ name: 'Sprint 1', story_ids: ['S2', 'S1'] }],
};

function envelope(data: Record<string, unknown>, ok = true): Envelope<Record<string, unknown>> {
  return { ok, llm_mode: 'n/a', warnings: [], data };
}

describe('planCounts', () => {
  it('counts what the plan actually has', () => {
    expect(planCounts(PLAN)).toEqual({ epics: 1, stories: 2, tasks: 3, sprints: 1 });
  });

  it('an unfinished conversation counts zero rather than throwing', () => {
    expect(planCounts({})).toEqual({ epics: 0, stories: 0, tasks: 0, sprints: 0 });
    expect(isEmptyPlan({})).toBe(true);
    expect(isEmptyPlan(PLAN)).toBe(false);
  });
});

describe('storiesOf', () => {
  it('follows the sprint order, not the story order', () => {
    expect(storiesOf(PLAN, PLAN.sprints![0]!).map((s) => s.id)).toEqual(['S2', 'S1']);
  });

  it('drops an id no story answers to', () => {
    expect(storiesOf(PLAN, { story_ids: ['S1', 'GONE'] }).map((s) => s.id)).toEqual(['S1']);
  });
});

describe('outcomeMessage', () => {
  it('names the file an export wrote', () => {
    expect(outcomeMessage(envelope({ path: '/tmp/plan.md' }))).toBe('Saved to /tmp/plan.md');
  });

  it('names the page a publish made', () => {
    expect(outcomeMessage(envelope({ url: 'https://notion.so/x' }))).toContain('https://notion.so/x');
  });

  it('counts what a sync created', () => {
    const message = outcomeMessage(
      envelope({
        destination: 'jira',
        stories_created: { S1: 'PROJ-1', S2: 'PROJ-2' },
        tasks_created: { T1: 'PROJ-3' },
        sprints_created: {},
        skipped_existing: 0,
      }),
    );
    expect(message).toBe('Created 2 stories, 1 task');
  });

  it('says an idempotent re-run created nothing, rather than reading as a failure', () => {
    const message = outcomeMessage(
      envelope({ destination: 'jira', stories_created: {}, tasks_created: {}, skipped_existing: 5 }),
    );
    expect(message).toContain('already on the board');
  });

  it('mentions what was skipped alongside what was made', () => {
    const message = outcomeMessage(
      envelope({ destination: 'azdevops', stories_created: { S1: '1' }, skipped_existing: 2 }),
    );
    expect(message).toBe('Created 1 story · 2 already existed');
  });

  it('reports a refusal in the words the engine used', () => {
    const failed: Envelope<Record<string, unknown>> = {
      ok: false,
      llm_mode: 'n/a',
      warnings: [],
      data: {},
      error: { type: 'ValueError', message: 'Jira is not configured' },
    };
    expect(outcomeMessage(failed)).toBe('Jira is not configured');
  });
});
