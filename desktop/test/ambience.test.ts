// The duck's arbiter and the beta-gate route map. Everything else in
// ambience.ts is a call, which the route tests cover on the Python side.

import { describe, expect, it } from 'vitest';
import { DuckVoice, HOLD_MS, PRIORITY_COACH, PRIORITY_EVENT, betaKeyFor } from '../src/renderer/ambience';

describe('DuckVoice', () => {
  it('takes a line and shows it', () => {
    const voice = new DuckVoice();
    expect(voice.say("Standup's up!", PRIORITY_EVENT, HOLD_MS, 0)).toBe(true);
    expect(voice.tick(0)?.text).toBe("Standup's up!");
  });

  it('drops the line once it has had its time', () => {
    const voice = new DuckVoice();
    voice.say('Saved it!', PRIORITY_EVENT, HOLD_MS, 0);
    expect(voice.tick(HOLD_MS - 1)).not.toBeNull();
    expect(voice.tick(HOLD_MS + 1)).toBeNull();
  });

  it('never lets coaching interrupt a quip', () => {
    const voice = new DuckVoice();
    voice.say("Report's ready!", PRIORITY_EVENT, HOLD_MS, 0);
    expect(voice.say('Try the arrow keys', PRIORITY_COACH, HOLD_MS, 10)).toBe(false);
    expect(voice.tick(10)?.text).toBe("Report's ready!");
  });

  it('lets a quip take the bubble from coaching', () => {
    const voice = new DuckVoice();
    voice.say('Try the arrow keys', PRIORITY_COACH, HOLD_MS, 0);
    expect(voice.say('Saved it!', PRIORITY_EVENT, HOLD_MS, 10)).toBe(true);
    expect(voice.tick(10)?.text).toBe('Saved it!');
  });

  it('does not restart the fade for a line already showing', () => {
    const voice = new DuckVoice();
    voice.say('Synced!', PRIORITY_EVENT, HOLD_MS, 0);
    voice.say('Synced!', PRIORITY_EVENT, HOLD_MS, 500);
    expect(voice.tick(HOLD_MS + 1)).toBeNull();
  });

  it('holds a sticky line until it is answered', () => {
    // A question that fades out unanswered is worse than one never asked.
    const voice = new DuckVoice();
    voice.saySticky('A diff needs you.', 0);
    expect(voice.tick(HOLD_MS * 100)?.text).toBe('A diff needs you.');
    expect(voice.sticky).toBe(true);
    voice.clearSticky();
    expect(voice.tick(0)).toBeNull();
  });

  it('lets nothing chattier take the bubble from a sticky line', () => {
    const voice = new DuckVoice();
    voice.saySticky('A diff needs you.', 0);
    expect(voice.say('Saved it!', PRIORITY_EVENT, HOLD_MS, 10)).toBe(false);
    expect(voice.tick(10)?.text).toBe('A diff needs you.');
  });

  it('clearSticky leaves an ordinary line alone', () => {
    const voice = new DuckVoice();
    voice.say('Saved it!', PRIORITY_EVENT, HOLD_MS, 0);
    voice.clearSticky();
    expect(voice.tick(0)?.text).toBe('Saved it!');
  });

  it('says nothing at all when muted', () => {
    const voice = new DuckVoice();
    voice.say('Saved it!', PRIORITY_EVENT, HOLD_MS, 0);
    voice.mute(true);
    expect(voice.tick(0)).toBeNull();
    expect(voice.say('Synced!', PRIORITY_EVENT, HOLD_MS, 10)).toBe(false);
  });

  it('an empty line is not a line', () => {
    // A blank status must not hold the bubble open.
    const voice = new DuckVoice();
    expect(voice.say('', PRIORITY_EVENT, HOLD_MS, 0)).toBe(false);
    expect(voice.tick(0)).toBeNull();
  });
});

describe('betaKeyFor', () => {
  it('names the gate a beta mode needs', () => {
    expect(betaKeyFor('/humans/ship')).toBe('ship');
    expect(betaKeyFor('/agents/advisor')).toBe('agent-advisor');
  });

  it("covers a mode's sub-pages — the gate is about the mode", () => {
    expect(betaKeyFor('/humans/ship/run')).toBe('ship');
    expect(betaKeyFor('/humans/performance/engineer')).toBe('performance');
  });

  it('is empty off the gated modes', () => {
    expect(betaKeyFor('/humans/standup')).toBe('');
    expect(betaKeyFor('/home')).toBe('');
  });

  it('does not gate a route that merely starts with the same letters', () => {
    expect(betaKeyFor('/humans/shipping-forecast')).toBe('');
  });
});
