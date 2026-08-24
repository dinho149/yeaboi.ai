// The chat's pure half: how a turn's NDJSON lines become bubbles, a stage and
// a verdict. The wire itself is pinned on the Python side (test_app_wire.py +
// contracts/v1/app_http.md); this is the renderer's reading of it.

import { describe, expect, it } from 'vitest';
import { type ChatLine, STAGE_RAIL, bubblesOf, reduceTurn, stageLabel } from '../src/renderer/chat';

const REPLY: ChatLine[] = [
  { type: 'op', op_id: 'op-1' },
  { type: 'token', text: 'How many ' },
  { type: 'token', text: 'of you?' },
  { type: 'question', text: 'How many of you?', number: 2 },
  { type: 'done', stage: 'intake' },
];

describe('reduceTurn', () => {
  it('keeps the op id so the turn stays cancellable', () => {
    expect(reduceTurn(REPLY).opId).toBe('op-1');
  });

  it('takes the new stage from the done line', () => {
    expect(reduceTurn(REPLY).stage).toBe('intake');
  });

  it('does not double the reply that streamed as tokens', () => {
    // The tokens and the question line are the same sentence; drawing both
    // would show it twice the moment the turn lands.
    expect(reduceTurn(REPLY).bubbles).toEqual([{ role: 'assistant', text: 'How many of you?' }]);
  });

  it('reports a cancelled turn without an error', () => {
    const turn = reduceTurn([{ type: 'op', op_id: 'x' }, { type: 'cancelled' }]);
    expect(turn.cancelled).toBe(true);
    expect(turn.error).toBe('');
    expect(turn.stage).toBeNull();
  });

  it('carries a classified error line through', () => {
    const turn = reduceTurn([{ type: 'error', message: 'Rate limited — wait a moment.' }]);
    expect(turn.error).toBe('Rate limited — wait a moment.');
  });
});

describe('bubblesOf', () => {
  it('renders a gate as its card followed by the verdict prompt', () => {
    const bubbles = bubblesOf([{ type: 'await_confirm', kind: 'intake_summary', prompt: 'Pick one.' }]);
    expect(bubbles).toEqual([
      { role: 'card', text: '', kind: 'intake_summary' },
      { role: 'assistant', text: 'Pick one.' },
    ]);
  });

  it('replays a whole conversation in order', () => {
    const bubbles = bubblesOf([
      { type: 'assistant', text: 'Hello!' },
      { type: 'user', text: 'a booking app' },
      { type: 'question', text: 'How many of you?', number: 2 },
      { type: 'artifact', kind: 'sprints' },
    ]);
    expect(bubbles.map((b) => b.role)).toEqual(['assistant', 'user', 'assistant', 'card']);
  });

  it('ignores line types that are not transcript rows', () => {
    expect(bubblesOf([{ type: 'op', op_id: 'x' }, { type: 'done', stage: 'chat' }])).toEqual([]);
  });
});

describe('stageLabel', () => {
  it('names every stage the backend can report', () => {
    for (const stage of ['intake', 'review', 'pipeline', 'epic', 'capacity', 'spike', 'chat'] as const) {
      expect(stageLabel(stage).length).toBeGreaterThan(0);
    }
  });

  it('the rail is a subset of the stages, in pipeline order', () => {
    expect(STAGE_RAIL.map((step) => step.stage)).toEqual(['intake', 'epic', 'pipeline', 'review', 'chat']);
  });
});
