/**
 * The board's server-truth layer: one external store, read through
 * `useSyncExternalStore`.
 *
 * ## Why a store and not Context
 *
 * A snapshot arrives whenever anything on the board changes — someone types,
 * someone joins, the timer ticks a second. Putting that in Context re-renders
 * the entire tree on every one of them. An external store lets a single `<Card>`
 * subscribe to a selector and re-render only when *its* slice moves.
 *
 * ## The monotonic guard
 *
 * `apply()` refuses any snapshot whose `revision` is lower than the one already
 * held. This fixes a real, visible bug in the polling board today: drag a card
 * to another column and a poll response that the server built *before* the move
 * lands a moment later and snaps the card back, until the next poll corrects
 * it. Ordering is not guaranteed — two in-flight requests can complete out of
 * order, and long-polling makes that more likely, not less, because a held
 * request and a fresh one race by design.
 *
 * Equal revisions are accepted rather than dropped: presence and typing
 * deliberately do **not** bump `revision` (see `RetroBoard.heartbeat` —
 * heartbeats fire about once a second and bumping would defeat change
 * detection), so dropping them would freeze the who's-here row.
 *
 * ## Deliberately not in here
 *
 * Local UI state — composer text, edit drafts, whether the rail is open, which
 * author is in focus. Those are `useState` in the component that owns them and
 * are never derived from a snapshot. That separation is what makes the old
 * `editingHere` hack unnecessary: an editor's draft simply is not reachable
 * from anything the server sends.
 */

/** The minimum shape the store needs. Both boards' snapshots satisfy it. */
export interface Revisioned {
  revision: number;
}

export interface BoardStore<S extends Revisioned> {
  /** Current snapshot, or `null` before the first one arrives. */
  getSnapshot(): S | null;
  /** Register a listener; returns the unsubscribe function. */
  subscribe(listener: () => void): () => void;
  /**
   * Accept a snapshot. Returns `false` when it was dropped as stale.
   * @param next the snapshot the server just sent
   */
  apply(next: S): boolean;
  /** Forget everything — used when the token changes, i.e. a different board. */
  reset(): void;
  /** How many snapshots have been dropped as stale. Surfaced in tests only. */
  staleDropped(): number;
}

export function createBoardStore<S extends Revisioned>(initial: S | null = null): BoardStore<S> {
  let snapshot: S | null = initial;
  let dropped = 0;
  const listeners = new Set<() => void>();

  function emit(): void {
    // Copy before iterating: a listener may unsubscribe during notification
    // (React does exactly this when a subscribed component unmounts), and
    // mutating a Set mid-iteration silently skips entries.
    for (const listener of [...listeners]) listener();
  }

  return {
    getSnapshot: () => snapshot,

    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },

    apply(next) {
      if (snapshot !== null && next.revision < snapshot.revision) {
        dropped += 1;
        return false;
      }
      snapshot = next;
      emit();
      return true;
    },

    reset() {
      snapshot = null;
      dropped = 0;
      emit();
    },

    staleDropped: () => dropped,
  };
}
