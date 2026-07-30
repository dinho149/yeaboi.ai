/**
 * The shape of what `/api/state` returns.
 *
 * Hand-written, unlike ./enums.ts. State shapes carry meaning a codegen cannot
 * express — which fields are per-viewer, which one deliberately does not bump
 * `revision`, which is a command rather than a value — and a confidently wrong
 * generated interface would be worse than an honest hand-written one.
 *
 * The drift guard is a fixture, not codegen: a Python test writes a real
 * snapshot to `tests/fixtures/state_snapshot.retro.json` and a `vitest
 * --typecheck` case asserts it `satisfies RetroState`, so a server-side shape
 * change fails the TypeScript build.
 *
 * Field names are snake_case throughout because they come off the wire that way
 * (Python dataclasses, `asdict`). Renaming them at the boundary would cost a
 * mapping layer and make every field harder to trace back to `board.py`.
 */

import type { CarriedStatuses, RetroGrids } from './enums';

/** The timer slice. Present on both boards. */
export interface TimerSlice {
  running: boolean;
  /** Unix seconds when it ends, or null when stopped. */
  end_epoch: number | null;
  /**
   * The server's clock at the moment the response was built.
   *
   * Clients derive an offset once and tick locally, which is why
   * `sharing/events.state_etag` can exclude this field. If it did not, the ETag
   * would change on every request and long-polling would degrade to a busy poll.
   */
  now_epoch: number;
  duration: number;
}

/** A host command pushed to every browser. `seq` makes it apply exactly once. */
export interface MusicCast {
  playing: boolean;
  channel: number;
  seq: number;
}

export interface BroadcastSlice {
  /** A theme name the host forced on the room, or null. */
  theme: string | null;
  music: MusicCast | null;
}

export interface Participant {
  name: string;
  avatar: string;
}

export interface TypingEntry {
  name: string;
  grid: RetroGrids;
}

/** One entry in the reaction ticker (`RetroBoard._reaction_events`). */
export interface ReactionEvent {
  id: number;
  emoji: string;
}

/** One sticky card. Mirrors `agent.state.RetroCard` plus the per-viewer fields. */
export interface RetroCard {
  /** Server-assigned. Never trusted from the browser — a peer cannot forge one. */
  id: string;
  grid: RetroGrids;
  /** Raw text. Escaped at render time, never pre-escaped — render it as a child. */
  text: string;
  author: string;
  /** ISO-8601 UTC. */
  created_at: string;
  origin: 'web' | 'ai' | 'carryover';
  /** emoji → count. Present on the live snapshot; a tuple in the frozen report. */
  reactions: Record<string, number>;
  /** Progress on a carried-over action item; empty for authoring-grid cards. */
  status: CarriedStatuses | '';
  /**
   * Whether the *requesting* participant owns this card — which is what drives
   * the edit and delete controls.
   *
   * Computed per viewer from their `pid`, which is why every response is a full
   * per-subscriber snapshot rather than a shared broadcast. Raw owner pids are
   * deliberately never put on the wire.
   */
  mine: boolean;
}

/** The full retro board snapshot. */
export interface RetroState {
  /**
   * Monotonic change counter.
   *
   * Presence and typing deliberately do NOT bump it (`RetroBoard.heartbeat`):
   * heartbeats fire about once a second and bumping would defeat change
   * detection. So `revision` going nowhere does not mean nothing changed —
   * which is why the store accepts equal revisions and only rejects lower ones.
   */
  revision: number;
  cards: RetroCard[];
  /** Last sprint's action items, surfaced for review. Not one of the grids. */
  carried: RetroCard[];
  presence: Participant[];
  typing: TypingEntry[];
  timer: TimerSlice;
  /**
   * Recent reactions, for the float-up animation.
   *
   * A bounded deque server-side (25 entries), so a client that has been away
   * sees only the tail. `id` is monotonic and is what the client keeps as a
   * high-water mark; without it a reconnect would replay the whole backlog at
   * once.
   */
  reaction_events: ReactionEvent[];
  broadcast: BroadcastSlice;
  /** Host froze card add/edit/delete/move for everyone. */
  locked: boolean;
}
