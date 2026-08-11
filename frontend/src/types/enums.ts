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
 * These are the sets the *server* validates against (a value from a participant
 * is rejected unless it is in one of them), so a literal union that disagreed
 * with one would let the client offer something the board will always refuse.
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
export const REACTION_EMOJIS = ["👍", "👎", "❤️", "🎉", "😂", "🔥", "😢", "🚀", "👀", "💯", "🙌", "🤔", "🐛", "✅", "⚠️", "🧠"] as const;
export type ReactionEmojis = (typeof REACTION_EMOJIS)[number];

/** Avatars a participant may choose. */
export const AVATARS = ["🤠", "👻", "🐙", "🦄", "🐸", "🦊", "🐼", "🐧", "🦖", "🐝", "🌮", "🍕", "👽", "🤖", "🎃", "🦩", "🐳", "🦉", "🌵", "🍄", "⚡", "🌈", "🪐", "🦆"] as const;
export type Avatars = (typeof AVATARS)[number];

/** Planning-poker card values, in deck order. */
export const POKER_DECK = ["0", "1", "2", "3", "5", "8", "13", "21", "?", "☕"] as const;
export type PokerDeck = (typeof POKER_DECK)[number];

/** Where a ticket's round is: voting → revealed, optionally via a duel. */
export const POKER_PHASES = ["voting", "revealed", "duel"] as const;
export type PokerPhases = (typeof POKER_PHASES)[number];

/** The open floor's lifecycle. The browser renders a different panel for each. */
export const DUEL_STATUSES = ["live", "transcribing", "done", "failed"] as const;
export type DuelStatuses = (typeof DUEL_STATUSES)[number];

/** What one correction does to a shared artifact. Server-validated, so it is generated rather than shipped in a boot payload — a payload would win at runtime and let a stale bundle offer an op the server rejects. */
export const EDIT_OPS = ["set", "append", "remove", "note", "field", "revert"] as const;
export type EditOps = (typeof EDIT_OPS)[number];

/** Every activity source a standup can collect from. */
export const ACTIVITY_SOURCES = ["jira", "azure_devops", "azdo_repos", "github", "local_git", "confluence", "notion"] as const;
export type ActivitySources = (typeof ACTIVITY_SOURCES)[number];

/** How a source is named to a user. Generated so the report, the progress steps and the exports agree — "azdo_repos".title() reads as "Azdo Repos", which looks like a different source from the one the steps just named. */
export const ACTIVITY_SOURCE_LABELS: Record<ActivitySources, string> = {
  "jira": "Jira",
  "azure_devops": "Azure DevOps tickets",
  "azdo_repos": "Azure DevOps code",
  "github": "GitHub",
  "local_git": "Local Git",
  "confluence": "Confluence",
  "notion": "Notion",
};

/**
 * The two-line block font, one entry per character: `[top, bottom]`.
 *
 * Mirrors `ui/shared/_ascii_font.py`, which is what the TUI sets every mode
 * title in. Rendered by `<Wordmark>`; characters absent here become gaps,
 * exactly as `render_ascii_text()` does.
 */
export const BLOCK_GLYPHS: Record<string, readonly [string, string]> = {
  "A": ["▄▀█", "█▀█"],
  "B": ["█▀▄", "█▄█"],
  "C": ["█▀▀", "█▄▄"],
  "D": ["█▀▄", "█▄▀"],
  "E": ["█▀▀", "██▄"],
  "F": ["█▀▀", "█▀░"],
  "G": ["█▀▀", "█▄█"],
  "H": ["█░█", "█▀█"],
  "I": ["█", "█"],
  "J": ["░░█", "█▄█"],
  "K": ["█▄▀", "█░█"],
  "L": ["█░░", "█▄▄"],
  "M": ["█▀▄▀█", "█░▀░█"],
  "N": ["█▄░█", "█░▀█"],
  "O": ["█▀█", "█▄█"],
  "P": ["█▀█", "█▀▀"],
  "Q": ["█▀█", "█▄▀"],
  "R": ["█▀█", "█▀▄"],
  "S": ["█▀", "▄█"],
  "T": ["▀█▀", "░█░"],
  "U": ["█░█", "█▄█"],
  "V": ["█░█", "▀▄▀"],
  "W": ["█░█░█", "▀▄▀▄▀"],
  "X": ["▀▄▀", "█░█"],
  "Y": ["█▄█", "░█░"],
  "Z": ["▀█", "█▄"],
  " ": ["░", "░"],
  "0": ["█▀█", "█▄█"],
  "1": ["▄█", "░█"],
  "2": ["▀▀█", "█▄▄"],
  "3": ["▀▀█", "▄▄█"],
  "4": ["█░█", "░▀█"],
  "5": ["█▀▀", "▄▄█"],
  "6": ["█▀▀", "█▄█"],
  "7": ["▀▀█", "░░█"],
  "8": ["█▀█", "█▄█"],
  "9": ["█▀█", "▀▀█"],
};

/**
 * `render_ascii_text()` output, straight from Python.
 *
 * Asserted by `Wordmark.test.tsx` so the TS renderer cannot drift from the
 * terminal's. Not for runtime use.
 */
export const WORDMARK_SAMPLES: Record<string, readonly [string, string]> = {
  "retro": ["█▀█ █▀▀ ▀█▀ █▀█ █▀█", "█▀▄ ██▄ ░█░ █▀▄ █▄█"],
  "poker": ["█▀█ █▀█ █▄▀ █▀▀ █▀█", "█▀▀ █▄█ █░█ ██▄ █▀▄"],
  "yeaboi": ["█▄█ █▀▀ ▄▀█ █▀▄ █▀█ █", "░█░ ██▄ █▀█ █▄█ █▄█ █"],
  "sprint 42": ["█▀ █▀█ █▀█ █ █▄░█ ▀█▀ ░ █░█ ▀▀█", "▄█ █▀▀ █▀▄ █ █░▀█ ░█░ ░ ░▀█ █▄▄"],
  "n/a": ["█▄░█    ▄▀█", "█░▀█    █▀█"],
};

/**
 * The six-row ANSI Shadow font, one entry per character.
 *
 * Mirrors `ui/shared/_ansi_font.py`. Covers A-Z and space only; a word
 * containing anything else has no setting in this face and the caller
 * falls back to BLOCK_GLYPHS, which is why the renderer returns null
 * rather than substituting a gap.
 */
export const SHADOW_GLYPHS: Record<string, readonly string[]> = {
  "A": [" █████╗ ", "██╔══██╗", "███████║", "██╔══██║", "██║  ██║", "╚═╝  ╚═╝"],
  "B": ["██████╗ ", "██╔══██╗", "██████╔╝", "██╔══██╗", "██████╔╝", "╚═════╝ "],
  "C": [" ██████╗", "██╔════╝", "██║     ", "██║     ", "╚██████╗", " ╚═════╝"],
  "D": ["██████╗ ", "██╔══██╗", "██║  ██║", "██║  ██║", "██████╔╝", "╚═════╝ "],
  "E": ["███████╗", "██╔════╝", "█████╗  ", "██╔══╝  ", "███████╗", "╚══════╝"],
  "F": ["███████╗", "██╔════╝", "█████╗  ", "██╔══╝  ", "██║     ", "╚═╝     "],
  "G": [" ██████╗ ", "██╔════╝ ", "██║  ███╗", "██║   ██║", "╚██████╔╝", " ╚═════╝ "],
  "H": ["██╗  ██╗", "██║  ██║", "███████║", "██╔══██║", "██║  ██║", "╚═╝  ╚═╝"],
  "I": ["██╗", "██║", "██║", "██║", "██║", "╚═╝"],
  "J": ["     ██╗", "     ██║", "     ██║", "██   ██║", "╚█████╔╝", " ╚════╝ "],
  "K": ["██╗  ██╗", "██║ ██╔╝", "█████╔╝ ", "██╔═██╗ ", "██║  ██╗", "╚═╝  ╚═╝"],
  "L": ["██╗     ", "██║     ", "██║     ", "██║     ", "███████╗", "╚══════╝"],
  "M": ["███╗   ███╗", "████╗ ████║", "██╔████╔██║", "██║╚██╔╝██║", "██║ ╚═╝ ██║", "╚═╝     ╚═╝"],
  "N": ["███╗   ██╗", "████╗  ██║", "██╔██╗ ██║", "██║╚██╗██║", "██║ ╚████║", "╚═╝  ╚═══╝"],
  "O": [" ██████╗ ", "██╔═══██╗", "██║   ██║", "██║   ██║", "╚██████╔╝", " ╚═════╝ "],
  "P": ["██████╗ ", "██╔══██╗", "██████╔╝", "██╔═══╝ ", "██║     ", "╚═╝     "],
  "Q": [" ██████╗ ", "██╔═══██╗", "██║   ██║", "██║▄▄ ██║", "╚██████╔╝", " ╚══▀▀═╝ "],
  "R": ["██████╗ ", "██╔══██╗", "██████╔╝", "██╔══██╗", "██║  ██║", "╚═╝  ╚═╝"],
  "S": ["███████╗", "██╔════╝", "███████╗", "╚════██║", "███████║", "╚══════╝"],
  "T": ["████████╗", "╚══██╔══╝", "   ██║   ", "   ██║   ", "   ██║   ", "   ╚═╝   "],
  "U": ["██╗   ██╗", "██║   ██║", "██║   ██║", "██║   ██║", "╚██████╔╝", " ╚═════╝ "],
  "V": ["██╗   ██╗", "██║   ██║", "██║   ██║", "╚██╗ ██╔╝", " ╚████╔╝ ", "  ╚═══╝  "],
  "W": ["██╗    ██╗", "██║    ██║", "██║ █╗ ██║", "██║███╗██║", "╚███╔███╔╝", " ╚══╝╚══╝ "],
  "X": ["██╗  ██╗", "╚██╗██╔╝", " ╚███╔╝ ", " ██╔██╗ ", "██╔╝ ██╗", "╚═╝  ╚═╝"],
  "Y": ["██╗   ██╗", "╚██╗ ██╔╝", " ╚████╔╝ ", "  ╚██╔╝  ", "   ██║   ", "   ╚═╝   "],
  "Z": ["███████╗", "╚══███╔╝", "  ███╔╝ ", " ███╔╝  ", "███████╗", "╚══════╝"],
  " ": ["    ", "    ", "    ", "    ", "    ", "    "],
};

/**
 * `render_shadow_text()` output, straight from Python. `null` means the
 * face cannot set that word. Not for runtime use.
 */
export const SHADOW_SAMPLES: Record<string, readonly string[] | null> = {
  "yeaboi": ["██╗   ██╗███████╗ █████╗ ██████╗  ██████╗ ██╗", "╚██╗ ██╔╝██╔════╝██╔══██╗██╔══██╗██╔═══██╗██║", " ╚████╔╝ █████╗  ███████║██████╔╝██║   ██║██║", "  ╚██╔╝  ██╔══╝  ██╔══██║██╔══██╗██║   ██║██║", "   ██║   ███████╗██║  ██║██████╔╝╚██████╔╝██║", "   ╚═╝   ╚══════╝╚═╝  ╚═╝╚═════╝  ╚═════╝ ╚═╝"],
  "retro": ["██████╗ ███████╗████████╗██████╗  ██████╗ ", "██╔══██╗██╔════╝╚══██╔══╝██╔══██╗██╔═══██╗", "██████╔╝█████╗     ██║   ██████╔╝██║   ██║", "██╔══██╗██╔══╝     ██║   ██╔══██╗██║   ██║", "██║  ██║███████╗   ██║   ██║  ██║╚██████╔╝", "╚═╝  ╚═╝╚══════╝   ╚═╝   ╚═╝  ╚═╝ ╚═════╝ "],
  "analysis": [" █████╗ ███╗   ██╗ █████╗ ██╗  ██╗   ██╗███████╗██╗███████╗", "██╔══██╗████╗  ██║██╔══██╗██║  ╚██╗ ██╔╝██╔════╝██║██╔════╝", "███████║██╔██╗ ██║███████║██║   ╚████╔╝ ███████╗██║███████╗", "██╔══██║██║╚██╗██║██╔══██║██║    ╚██╔╝  ╚════██║██║╚════██║", "██║  ██║██║ ╚████║██║  ██║███████╗██║   ███████║██║███████║", "╚═╝  ╚═╝╚═╝  ╚═══╝╚═╝  ╚═╝╚══════╝╚═╝   ╚══════╝╚═╝╚══════╝"],
  "team retro": ["████████╗███████╗ █████╗ ███╗   ███╗    ██████╗ ███████╗████████╗██████╗  ██████╗ ", "╚══██╔══╝██╔════╝██╔══██╗████╗ ████║    ██╔══██╗██╔════╝╚══██╔══╝██╔══██╗██╔═══██╗", "   ██║   █████╗  ███████║██╔████╔██║    ██████╔╝█████╗     ██║   ██████╔╝██║   ██║", "   ██║   ██╔══╝  ██╔══██║██║╚██╔╝██║    ██╔══██╗██╔══╝     ██║   ██╔══██╗██║   ██║", "   ██║   ███████╗██║  ██║██║ ╚═╝ ██║    ██║  ██║███████╗   ██║   ██║  ██║╚██████╔╝", "   ╚═╝   ╚══════╝╚═╝  ╚═╝╚═╝     ╚═╝    ╚═╝  ╚═╝╚══════╝   ╚═╝   ╚═╝  ╚═╝ ╚═════╝ "],
  "sprint 42": null,
};
