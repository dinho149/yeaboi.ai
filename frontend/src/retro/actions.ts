/**
 * Every write the retro board can make, in one place.
 *
 * Each endpoint answers with `{ok, state}` — a **full fresh snapshot**, built
 * after the mutation. Feeding that straight back into the store is what makes
 * your own actions feel instant: you see the result of your own edit on the
 * POST's response rather than waiting up to a long-poll for it to come round.
 *
 * The store's monotonic guard is what makes that safe. A POST response and a
 * parked long-poll race by design, so the two can land out of order; the guard
 * drops whichever carries the older revision. This is the fix for a bug you can
 * watch happen on the current board — drag a card, and a poll response the
 * server built *before* the move snaps it back for a beat.
 */

import { postJSON, type ApiResult, type Session } from '../runtime/api';
import type { BoardStore } from '../store/boardStore';
import type { CarriedStatuses, RetroGrids } from '../types/enums';
import type { RetroState } from '../types/board';

/** The envelope every mutating retro endpoint returns. */
interface StateEnvelope {
  ok?: boolean;
  state: RetroState;
}

/** `/api/react` adds one field: whether *you* now hold that reaction. */
interface ReactEnvelope extends StateEnvelope {
  reacted: boolean;
}

export interface RetroActions {
  addCard(grid: RetroGrids, text: string, author: string): Promise<boolean>;
  editCard(cardId: string, text: string): Promise<boolean>;
  deleteCard(cardId: string): Promise<boolean>;
  moveCard(cardId: string, grid: RetroGrids, index: number): Promise<boolean>;
  /** Toggles. Resolves to whether the reaction is now yours. */
  react(cardId: string, emoji: string): Promise<boolean>;
  setCarriedStatus(itemId: string, status: CarriedStatuses): Promise<boolean>;
  startTimer(seconds: number): Promise<boolean>;
  stopTimer(): Promise<boolean>;
  setLocked(locked: boolean): Promise<boolean>;
  castTheme(theme: string): Promise<boolean>;
  castMusic(playing: boolean, channel: number): Promise<boolean>;
  /**
   * Ask for action items.
   *
   * Reads the feedback columns, weights each card by how many people reacted to
   * it, and appends what it makes of them as `origin: "ai"` cards — plus last
   * sprint's "Carried Over" items, which is the half of that loop the board has
   * never been able to close. Resolves to the line to show, because this is the
   * one action whose outcome is not visible in the cards it produces (an
   * unconfigured LLM still adds something, and should say so).
   */
  suggestActions(): Promise<string>;
}

/**
 * Bind the write endpoints to a session and a store.
 *
 * Plain function rather than a hook: it closes over nothing that changes per
 * render, and callers memoise it once with `useMemo`. Keeping it out of the
 * hook rules also means the action layer is directly unit-testable with a fake
 * `fetch` and no renderer at all.
 */
export function createRetroActions(session: Session, store: BoardStore<RetroState>): RetroActions {
  async function mutate(path: string, body: Record<string, unknown>): Promise<ApiResult<StateEnvelope>> {
    const result = await postJSON<StateEnvelope>(session, path, body);
    if (result.ok && result.data.state) store.apply(result.data.state);
    return result;
  }

  return {
    async addCard(grid, text, author) {
      const trimmed = text.trim();
      if (!trimmed) return false;
      return (await mutate('/api/cards', { grid, text: trimmed, author })).ok;
    },

    async editCard(cardId, text) {
      const trimmed = text.trim();
      // An empty edit is a delete in disguise, and the server rejects it with a
      // 400. Treating it as "nothing to do" keeps the card and its text intact,
      // which is what someone who cleared the box and hit Save almost never
      // means — the ✕ button is right there.
      if (!trimmed) return false;
      return (await mutate('/api/card/edit', { card_id: cardId, text: trimmed })).ok;
    },

    async deleteCard(cardId) {
      return (await mutate('/api/card/delete', { card_id: cardId })).ok;
    },

    async moveCard(cardId, grid, index) {
      return (await mutate('/api/card/move', { card_id: cardId, grid, index })).ok;
    },

    async react(cardId, emoji) {
      const result = await postJSON<ReactEnvelope>(session, '/api/react', { card_id: cardId, emoji });
      if (!result.ok) return false;
      if (result.data.state) store.apply(result.data.state);
      return Boolean(result.data.reacted);
    },

    async setCarriedStatus(itemId, status) {
      return (await mutate('/api/carried/status', { item_id: itemId, status })).ok;
    },

    async startTimer(seconds) {
      return (await mutate('/api/timer', { action: 'start', duration: seconds })).ok;
    },

    async stopTimer() {
      return (await mutate('/api/timer', { action: 'stop' })).ok;
    },

    async setLocked(locked) {
      return (await mutate('/api/admin/lock', { locked })).ok;
    },

    async castTheme(theme) {
      return (await mutate('/api/admin/broadcast', { theme })).ok;
    },

    async castMusic(playing, channel) {
      return (await mutate('/api/admin/broadcast', { music: { playing, channel } })).ok;
    },

    async suggestActions() {
      const result = await postJSON<StateEnvelope & { message?: string }>(session, '/api/admin/suggest', {});
      if (result.ok && result.data.state) store.apply(result.data.state);
      if (!result.ok) return 'Could not reach the board — nothing was added.';
      return result.data.message ?? '';
    },
  };
}
