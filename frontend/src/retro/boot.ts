/**
 * The retro board's boot payload.
 *
 * ## What is *not* in here, and why
 *
 * The plan's sketch had `board_config()` shipping the grids, statuses, emojis,
 * avatars and themes in the island. They are not here: every one of those is a
 * server-validated tuple, and `types/enums.ts` is generated from the very same
 * Python constants with a `--check` in CI. Carrying them twice would give the
 * same tuple two sources of truth that disagree silently — the island would
 * name a fifth grid the bundle has no label or column for, and the page would
 * render a board the server accepts writes to but the client cannot show.
 *
 * So the split is: **anything a codegen can pin comes from `enums.ts`**, and the
 * island carries only what it cannot — free-form word lists, the stream library,
 * and the per-session titles. A stale bundle then fails `gen_web_types.py
 * --check` at build time instead of misrendering at ceremony time.
 */

import { requireBoot } from '../runtime/boot';
import type { Channel } from '../hooks/useMusic';

export interface RetroBoot {
  /** Board heading — the project or sprint the retro belongs to. */
  title: string;
  /** Sprint name, shown beside the title. May be empty. */
  sprint: string;
  /** Random-name word lists. From `retro/board.py`, so the TUI matches. */
  adjectives: string[];
  nouns: string[];
  /** The same SomaFM/SRG stations the TUI plays (`yeaboi.music.CHANNELS`). */
  musicChannels: Channel[];
}

export function readRetroBoot(): RetroBoot {
  return requireBoot<RetroBoot>();
}
