/**
 * Builders for retro snapshots.
 *
 * Every field is defaulted to something valid so a test states only what it is
 * about — a case that cares about card ownership should not have to invent a
 * timer, and a reader should not have to work out which of twelve fields is the
 * one under test.
 */

import type { Participant, RetroCard, RetroState, TypingEntry } from '../types/board';
import type { RetroGrids } from '../types/enums';

let nextId = 0;

export function card(overrides: Partial<RetroCard> = {}): RetroCard {
  nextId += 1;
  return {
    id: `c${nextId}`,
    grid: 'went_well' as RetroGrids,
    text: 'a card',
    author: 'Ada',
    created_at: '2026-07-29T10:00:00+00:00',
    origin: 'web',
    reactions: {},
    status: '',
    mine: false,
    ...overrides,
  };
}

export function state(overrides: Partial<RetroState> = {}): RetroState {
  return {
    revision: 1,
    cards: [],
    carried: [],
    presence: [],
    typing: [],
    timer: { running: false, end_epoch: null, now_epoch: 1_780_000_000, duration: 0 },
    reaction_events: [],
    broadcast: { theme: null, music: null },
    locked: false,
    ...overrides,
  };
}

export const person = (name: string, avatar = '🙂'): Participant => ({ name, avatar });

export const typing = (name: string, grid: RetroGrids): TypingEntry => ({ name, grid });
