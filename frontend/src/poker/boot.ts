/**
 * The poker board's boot payload.
 *
 * Same split as the retro board: **anything a codegen can pin comes from
 * `enums.ts`** — the deck, the avatars, the palettes — and the island carries
 * only what it cannot. Shipping the deck here as well would give one
 * server-validated tuple two sources of truth, and the island would win at
 * runtime, so a stale bundle would offer a card the board refuses to accept.
 */

import { requireBoot } from '../runtime/boot';
import type { PageChrome } from '../shared/chrome';
import type { Channel } from '../hooks/useMusic';

export interface PokerBoot {
  /**
   * Masthead and footer, the same shape every yeaboi surface wears.
   *
   * Static facts only — see the note on `RetroBoot.chrome`. The ticket
   * position and the vote count move during a session and come over
   * `/api/state`.
   */
  chrome: PageChrome;
  /** What the batch is: a sprint, a query, a label. Shown beside the title. */
  scope: string;
  /** Random-name word lists, shared with retro so the join feels identical. */
  adjectives: string[];
  nouns: string[];
  /** The same SomaFM/SRG stations the TUI plays (`yeaboi.music.CHANNELS`). */
  musicChannels: Channel[];
}

export function readPokerBoot(): PokerBoot {
  return requireBoot<PokerBoot>();
}
