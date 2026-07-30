/*
 * GENERATED FILE — do not edit.
 *
 * Regenerate with `uv run python scripts/gen_web_types.py` after changing any of
 * the server-validated tuples in retro/board.py or poker/board.py. CI runs the
 * same script with --check and fails if this file is stale.
 *
 * Only the enums are generated. State shapes are hand-written in ./board.ts,
 * because they carry semantics a codegen cannot express — and a confidently
 * wrong generated interface is worse than an honest hand-written one.
 *
 * These are the sets the *server* validates against (a value from a LAN peer is
 * rejected unless it is in one of them), so a literal union that disagreed with
 * one would let the client offer something the board will always refuse.
 */

/** The four retro columns, in display order. */
export const RETRO_GRIDS = ["went_well", "didnt_go_well", "action_items", "demos"] as const;
export type RetroGrids = (typeof RETRO_GRIDS)[number];

/** Human-facing column headings. */
export const RETRO_GRID_LABELS: Record<RetroGrids, string> = {
  "went_well": "What went well",
  "didnt_go_well": "What didn't go well",
  "action_items": "Action items",
  "demos": "Demos",
};

/** Statuses a carried-over action item can be set to. */
export const CARRIED_STATUSES = ["pending", "done", "in_progress", "carried_over", "not_relevant"] as const;
export type CarriedStatuses = (typeof CARRIED_STATUSES)[number];

/** Carried-item status labels. */
export const CARRIED_STATUS_LABELS: Record<CarriedStatuses, string> = {
  "pending": "Pending",
  "done": "Done",
  "in_progress": "In Progress",
  "carried_over": "Carried Over",
  "not_relevant": "Not Relevant",
};

/** Palettes the host may broadcast. Mirrors palette.css. */
export const RETRO_THEMES = ["midnight", "light", "solarized", "synthwave", "forest"] as const;
export type RetroThemes = (typeof RETRO_THEMES)[number];

/** The only emoji a card reaction may use. */
export const REACTION_EMOJIS = ["👍", "❤️", "🎉", "😂", "🔥", "😢", "🚀", "👀"] as const;
export type ReactionEmojis = (typeof REACTION_EMOJIS)[number];

/** Avatars a participant may choose. */
export const AVATARS = ["🤠", "👻", "🐙", "🦄", "🐸", "🦊", "🐼", "🐧", "🦖", "🐝", "🌮", "🍕", "👽", "🤖", "🎃", "🦩", "🐳", "🦉", "🌵", "🍄", "⚡", "🌈", "🪐", "🦆"] as const;
export type Avatars = (typeof AVATARS)[number];

/** Planning-poker card values, in deck order. */
export const POKER_DECK = ["0", "1", "2", "3", "5", "8", "13", "21", "?", "☕"] as const;
export type PokerDeck = (typeof POKER_DECK)[number];
