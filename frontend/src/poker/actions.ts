/**
 * Every write the poker board can make, in one place.
 *
 * Same contract as the retro board's: each endpoint answers with a **full fresh
 * snapshot** built after the mutation, and feeding that straight back into the
 * store is what makes your own actions feel instant. The store's monotonic
 * guard is what makes it safe — a POST response and a parked long-poll race by
 * design, and the guard drops whichever carries the older revision.
 *
 * ## The one thing that is not like retro
 *
 * Most of these are host-only, and the server answers a rejected host action
 * with `{error: "..."}` rather than a failure status — "reveal the votes first",
 * "need two different numeric votes to duel". Those are the reasons a host most
 * needs to see, so {@link PokerActions} resolves to the error string instead of
 * swallowing it into a boolean. Guests never see them: a guest's admin secret
 * is the empty string, so their request fails the compare and returns 403, and
 * `''` is what a 403 resolves to.
 */

import { postJSON, type Session } from '../runtime/api';
import type { BoardStore } from '../store/boardStore';
import type { PokerState } from '../types/board';

/** The envelope every mutating poker endpoint returns. */
interface StateEnvelope {
  ok?: boolean;
  state?: PokerState;
  /** A human-readable refusal — shown to the host, never to a guest. */
  error?: string;
}

/** What the host typed into the edit-ticket modal. Only changed fields are sent. */
export interface TicketEdit {
  summary?: string;
  description?: string;
  points?: number;
  state?: string;
  assignee?: string;
  type?: string;
  acceptance?: string;
}

/**
 * Every write, resolving to the server's refusal reason or `''` for success.
 *
 * `''` rather than a boolean because "it worked" and "it failed and here is why"
 * are the only two outcomes a caller acts on, and a boolean would force every
 * call site to re-fetch the reason it just threw away.
 */
export interface PokerActions {
  /** Cast, or withdraw by voting your current value again. */
  vote(value: string, current: string): Promise<string>;
  reveal(): Promise<string>;
  revote(): Promise<string>;
  goto(index: number): Promise<string>;
  finalize(points: number): Promise<string>;
  editTicket(key: string, edit: TicketEdit): Promise<string>;
  askAi(): Promise<string>;
  openDuel(turnSeconds: number): Promise<string>;
  nextTurn(): Promise<string>;
  closeDuel(): Promise<string>;
  /** Tell the room this browser's mic is (or is not) recording. */
  setMic(on: boolean): Promise<string>;
  startTimer(seconds: number): Promise<string>;
  stopTimer(): Promise<string>;
  setLocked(locked: boolean): Promise<string>;
  castTheme(theme: string): Promise<string>;
  castMusic(playing: boolean, channel: number): Promise<string>;
}

export function createPokerActions(session: Session, store: BoardStore<PokerState>): PokerActions {
  async function mutate(path: string, body: Record<string, unknown> = {}): Promise<string> {
    const result = await postJSON<StateEnvelope>(session, path, body);
    if (!result.ok) {
      // 0 is a network-level failure (tunnel dropped, offline). Saying so beats
      // a bare "failed", because the two have completely different fixes.
      return result.status === 0 ? 'Could not reach the board.' : '';
    }
    if (result.data.state) store.apply(result.data.state);
    return result.data.error ?? '';
  }

  return {
    async vote(value, current) {
      // Tapping your selected card again withdraws the vote — the same gesture
      // both ways, so there is no separate "clear" control to find.
      return mutate(value === current ? '/api/vote/clear' : '/api/vote', { value });
    },

    reveal: () => mutate('/api/admin/reveal'),
    revote: () => mutate('/api/admin/revote'),
    goto: (index) => mutate('/api/admin/goto', { index }),
    finalize: (points) => mutate('/api/admin/finalize', { points }),
    editTicket: (key, edit) => mutate('/api/admin/ticket/edit', { key, ...edit }),
    askAi: () => mutate('/api/admin/ai'),
    // `seconds`, not `turn_seconds`. The board *field* is `turn_seconds` and the
    // request key is not, which is exactly the sort of mismatch that costs
    // nothing to get wrong: the server defaults to 90 rather than refusing, so
    // picking 60 silently gave everyone a 90-second turn.
    openDuel: (seconds) => mutate('/api/admin/duel/open', { seconds }),
    nextTurn: () => mutate('/api/admin/duel/next'),
    closeDuel: () => mutate('/api/admin/duel/close'),
    setMic: (on) => mutate('/api/duel/mic', { on }),
    startTimer: (seconds) => mutate('/api/timer', { action: 'start', duration: seconds }),
    stopTimer: () => mutate('/api/timer', { action: 'stop' }),
    setLocked: (locked) => mutate('/api/admin/lock', { locked }),
    castTheme: (theme) => mutate('/api/admin/broadcast', { theme }),
    castMusic: (playing, channel) => mutate('/api/admin/broadcast', { music: { playing, channel } }),
  };
}
