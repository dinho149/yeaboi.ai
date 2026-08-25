/**
 * The shape of what `/api/state` returns.
 *
 * Hand-written, unlike ./enums.ts. State shapes carry meaning a codegen cannot
 * express — which fields are per-viewer, which one deliberately does not bump
 * `revision`, which is a command rather than a value — and a confidently wrong
 * generated interface would be worse than an honest hand-written one.
 *
 * The drift guard is a fixture, not codegen. `tests/unit/test_web_wire_shapes.py`
 * drives a real board through a real round and writes the resulting snapshots to
 * `contracts/web/fixtures/`; `src/test/fixtures/wire.ts` imports them and asserts each
 * `satisfies` its interface here, so `npm run typecheck` fails when the server
 * stops sending a field this file promises.
 *
 * What that catches and what it does not: a removed or renamed field fails,
 * because the fixture then lacks something the interface requires. A field the
 * server *adds* does not, because TypeScript only excess-property-checks fresh
 * object literals and an imported JSON module is not one. So the guard is a
 * one-way ratchet, which is the direction that actually breaks a board.
 *
 * Field names are snake_case throughout because they come off the wire that way
 * (Python dataclasses, `asdict`). Renaming them at the boundary would cost a
 * mapping layer and make every field harder to trace back to `board.py`.
 */

import type { CarriedStatuses, DuelStatuses, PokerPhases, RetroGrids } from './enums';

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

// ───────────────────────────── Planning poker ─────────────────────────────

/**
 * Which part of the round the room is in. Drives almost every poker control.
 *
 * Generated from `poker/board.py`'s own tuple rather than written out here, so
 * a phase the server can enter and the browser has never heard of is a build
 * failure instead of a blank panel.
 */
export type PokerPhase = PokerPhases;

/**
 * One ticket, as it appears in the live snapshot.
 *
 * The full board row, so it carries session-time result fields the peek view
 * (`TicketView`) deliberately omits. `source` is the tracker it came from and is
 * only read to warn that saving an Azure DevOps description flattens its
 * formatting.
 */
export interface PokerTicket {
  key: string;
  summary: string;
  description_text: string;
  acceptance_text: string;
  type: string;
  state: string;
  assignee: string;
  url: string;
  story_points: number | null;
  initial_points: number | null;
  final_points: number | null;
  estimated: boolean;
  ai_note: string;
  source?: string;
  rev: number;
}

/**
 * The read-only projection `GET /api/ticket` answers with.
 *
 * Any token-holder may read any ticket in the batch — the same audience that
 * sees the live one — but only display fields cross the wire. Round internals
 * (accepted votes, the AI note, the duel record) stay board-internal, which is
 * why this is a narrower type than {@link PokerTicket} rather than the same one.
 */
export interface TicketView {
  index: number;
  rev: number;
  key: string;
  summary: string;
  description_text: string;
  acceptance_text: string;
  type: string;
  story_points: number | null;
  state: string;
  assignee: string;
  url: string;
  estimated: boolean;
  final_points: number | null;
}

/**
 * What `GET /api/invite` answers with, on both boards.
 *
 * Shared shape, one endpoint per server. It is a fetch rather than a field in
 * the boot payload for a reason worth keeping written down: `GET /` is
 * unauthenticated, so anything in the JSON island is readable by anyone who
 * reaches the board, token or not — putting the join code there would be the
 * gate handing out its own key.
 *
 * There is no host link here and there must never be one. It carries the admin
 * secret, and every participant can read this response.
 */
export interface InviteInfo {
  /** The code a teammate types on the gate, e.g. `"K3P9-2QXA"`. */
  joinCode: string;
  /**
   * The whole invite as one URL: `shareUrl` with `#code=<joinCode>` on the end.
   *
   * What the panel copies, and what the QR encodes. Composed server-side by
   * `sharing.access.invite_url` rather than here, so the format has exactly one
   * implementation — a fragment the emitter and the parser disagree about fails
   * silently, with the link opening the gate and the autofill just not
   * happening. `""` until the tunnel is up, same as `shareUrl`.
   */
  inviteUrl: string;
  /**
   * The token-free URL to hand out — the board's Cloudflare tunnel address.
   *
   * The server binds loopback, so the tunnel is the only address that means
   * anything to a teammate; the host's own browser reaches the board at
   * `127.0.0.1`, and handing *that* back would send the reader to their own
   * machine. Falls back to the requesting host only before the tunnel is up.
   */
  shareUrl: string;
  /**
   * Why there is no `shareUrl`, when there is none.
   *
   * An empty url means four different things and the browser cannot tell them
   * apart: the tunnel is still coming up (up to a minute), it gave up, sharing
   * was turned off with `YEABOI_NO_TUNNEL`, or — `"ready"` — there is a url and
   * this says nothing. Optional, because poker's board does not send it.
   */
  shareState?: 'ready' | 'pending' | 'failed' | 'off';
}

/** A rail row: enough to list and mark a ticket, never its body. */
export interface TicketMeta {
  key: string;
  summary: string;
  estimated: boolean;
  final_points: number | null;
  story_points: number | null;
  /**
   * Per-ticket content revision, bumped only on edit and finalize.
   *
   * The board-wide `revision` moves on every vote and heartbeat, so a peek
   * cache keyed on that would refetch a ticket body several times a second.
   */
  rev: number;
}

/**
 * One seat at the table.
 *
 * **Vote secrecy is enforced by which fields exist**, not by blanking them:
 * while `phase === 'voting'` the server sends `voted` and no `value` at all, so
 * there is nothing in the payload for a devtools-literate teammate to read
 * early. Both are optional here because one snapshot cannot type both phases,
 * and `phase` is the discriminant.
 */
export interface PokerVote {
  name: string;
  avatar: string;
  /** Whether they have voted. Present only while voting. */
  voted?: boolean;
  /** What they voted. Present only once revealed. */
  value?: string;
}

/** The AI's read on the current ticket. `pending` is what guards double-clicks. */
export interface AiPerspective {
  pending: boolean;
  /** False when the engine fell back — then `note` is only the reason why. */
  from_llm: boolean;
  note: string;
  suggested: number | null;
  confidence: string;
  evidence: string[];
}

/** A duelist, as everyone else sees them. The pid behind this never ships. */
export interface Duelist {
  name: string;
  avatar: string;
  value: string;
}

/** Who is recording right now — the host's room mic and each duelist's own. */
export interface DuelRecording {
  host: boolean;
  low: boolean;
  high: boolean;
}

/** The open floor: the low and high voters argue, on the clock. */
export interface DuelSlice {
  /**
   * Generated from `poker/board.py`'s `DUEL_STATUSES`.
   *
   * Hand-written, this said `'error'`. The board writes
   * `"done" if clean else "failed"`, and `error` below is the *message*, not
   * the state — so every failed duel fell through to the client's catch-all
   * branch and the union was decorative. The wire fixtures structurally cannot
   * catch that (a JSON import types every string as `string`), which is why
   * this one goes through the codegen instead.
   */
  status: DuelStatuses;
  /** Whose turn it is. The low voter always speaks first. */
  turn: 'low' | 'high';
  /** Monotonic within a duel — the recorder tags each upload with it. */
  turn_no: number;
  turn_seconds: number;
  low: Duelist;
  high: Duelist;
  recording: DuelRecording;
  transcript: string;
  error: string;
  /**
   * `'low'`, `'high'`, or `''` — whether *this* browser is a duelist.
   *
   * Server-computed from the viewer's pid rather than matched on name, so two
   * people who both called themselves "Sam" cannot end up sharing a turn.
   */
  mine_role: 'low' | 'high' | '';
}

export interface PokerProgress {
  estimated: number;
  total: number;
}

/** The full planning-poker snapshot. */
export interface PokerState {
  revision: number;
  phase: PokerPhase;
  ticket_index: number;
  ticket_count: number;
  /** The ticket being voted on, or null when the batch is empty. */
  ticket: PokerTicket | null;
  tickets_meta: TicketMeta[];
  votes: PokerVote[];
  /** Your own vote, echoed back to you — `''` when you have not voted. */
  mine_value: string;
  /** Deck-ordered `{value: count}`, empties dropped. Empty while voting. */
  distribution: Record<string, number>;
  /** Null while voting, and when nobody cast a numeric vote. */
  median: number | null;
  /** `median` snapped to the nearest deck value. */
  suggestion: number | null;
  ai: AiPerspective;
  duel: DuelSlice | null;
  progress: PokerProgress;
  presence: Participant[];
  timer: TimerSlice;
  broadcast: BroadcastSlice;
  /** Host froze voting for everyone. */
  locked: boolean;
  /** The host is recording the session, whether or not a floor is open. */
  room_mic: boolean;
  /** Last tracker-write error. Rendered to the host only. */
  notice: string;
}


// ── Ship board ─────────────────────────────────────────────────────────────
// A read-only projection of one supervised story → PR run (`ship/board.py`).
// Guests watch: the phase checklist, the agent's live activity, the scrubbed
// diff, the validation verdict. Everything sensitive is scrubbed server-side.

/** One progress component — the five-phase checklist. Raw `analysis_component`. */
export interface ShipPhaseEvent {
  component_id: string;
  label: string;
  status: string;
  detail?: string;
}

/** One safe agent-activity entry. Never carries a tool input or command output. */
export interface ShipActivity {
  /** `text` (an assistant line), `tool` (a tool name), or `system` (the model). */
  kind: string;
  text?: string;
  name?: string;
}

/** The deterministic validation verdict shown at the gate. Tail is scrubbed. */
export interface ShipValidationView {
  configured: boolean;
  command: string;
  passed: boolean;
  exit_code: number;
  output_tail: string;
}

/** A watcher on the board — self-asserted name/avatar, no pid crosses the wire. */
export interface ShipWatcher {
  name: string;
  avatar: string;
}

/** The full ship-board snapshot. */
export interface ShipState {
  revision: number;
  run_id: string;
  /** planned | running | awaiting_approval | approved | rejected | cancelled | failed | starting */
  status: string;
  story: string;
  project: string;
  phases: ShipPhaseEvent[];
  activity: ShipActivity[];
  diff_stat: string;
  /** The scrubbed patch. Rendered as text content, never as markup. */
  diff_text: string;
  validation: ShipValidationView;
  cost_usd: number;
  /** `[kind, severity, label]` triples — labels only, never transcript content. */
  findings: string[][];
  pr_url: string;
  /** `''` (open) | approved | rejected */
  gate_resolution: string;
  gate_comment: string;
  rejection_count: number;
  warnings: string[];
  branch: string;
  presence: ShipWatcher[];
}
