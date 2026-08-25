/**
 * Builders for poker snapshots.
 *
 * Seeded from the **real wire fixtures**, not from hand-written literals. That
 * matters more here than the convenience: the join-code bug shipped because two
 * tests each invented their own contract and agreed with each other while
 * agreeing with no server. `contracts/web/fixtures/` is written by a Python test
 * driving a real `PokerBoard`, so a test built on it cannot describe a board
 * that does not exist.
 *
 * Every override is shallow and explicit, so a case states only what it is
 * about — a test about the deck should not have to invent a duel.
 */

import type { PokerState, PokerTicket, PokerVote } from '../types/board';
import { POKER_DUEL_WIRE, POKER_REVEALED_WIRE, POKER_VOTING_WIRE } from './fixtures/wire';

/** A round in progress: two seats, no values on the wire. */
export function voting(overrides: Partial<PokerState> = {}): PokerState {
  return { ...POKER_VOTING_WIRE, ...overrides };
}

/** Votes public, the AI has weighed in, the floor has not opened. */
export function revealed(overrides: Partial<PokerState> = {}): PokerState {
  return { ...POKER_REVEALED_WIRE, ...overrides };
}

/** The floor is open, viewed as the low voter. */
export function dueling(overrides: Partial<PokerState> = {}): PokerState {
  return { ...POKER_DUEL_WIRE, ...overrides };
}

/** A seat mid-round: known to be present, `voted` says whether they have. */
export const seat = (name: string, voted = false, avatar = '🙂'): PokerVote => ({ name, avatar, voted });

/** A seat post-reveal, carrying its value. */
export const shown = (name: string, value: string, avatar = '🙂'): PokerVote => ({ name, avatar, value });

export function ticket(overrides: Partial<PokerTicket> = {}): PokerTicket {
  return {
    key: 'YB-1',
    summary: 'Long-poll the board',
    description_text: 'The tunnel buffers SSE.',
    acceptance_text: '',
    type: 'Story',
    state: 'To Do',
    assignee: 'Ada',
    url: 'https://example.invalid/browse/YB-1',
    story_points: null,
    initial_points: null,
    final_points: null,
    estimated: false,
    ai_note: '',
    rev: 0,
    ...overrides,
  };
}
