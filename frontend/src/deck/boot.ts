/**
 * The slide deck's boot payload — the whole deck, in one JSON island.
 *
 * Unlike the live boards there is no server here. An exported deck is a single
 * file on someone's disk, so this payload is not a *starting* state that gets
 * refined by polling; it is the entire document. Everything the deck will ever
 * show is in it, which is why the palettes ship whole rather than as a name:
 * there is nothing to look them up from.
 *
 * Mirrors `deck_payload()` in `src/yeaboi/reporting/presentation.py`.
 */

import { requireBoot } from '../runtime/boot';

/** The six colour roles a reporting palette defines (`themes.ROLE_KEYS`). */
export interface DeckPalette {
  /** Page background. */
  bg1: string;
  /** The second stop of the background wash — what makes the palettes distinct. */
  bg2: string;
  /** Body text. */
  fg: string;
  /** Secondary text. */
  muted: string;
  /** Primary accent. */
  accent: string;
  /** Bright variant — big numbers, the headline. */
  accent2: string;
}

export type SlideType = 'title' | 'summary' | 'metrics' | 'cards' | 'list' | 'thanks';

export interface DeckSlide {
  type: SlideType;
  /** Section mark, chosen by the LLM design pass. */
  emoji?: string;
  /**
   * Which act of the deck this slide belongs to — rendered as the eyebrow.
   * Absent on the title and thank-you slides, which are not part of either.
   */
  section?: string;
  title?: string;
  /** Thank-you slide only: the project name, under the wordmark. */
  subtitle?: string;
  /** Title slide only. */
  headline?: string;
  /** Position within a paginated run, as `[i, n]`. Absent when there is one page. */
  page?: [number, number];
  /** Summary slide: one paragraph per sentence-level point. */
  points?: string[];
  /**
   * A whole-paragraph summary.
   *
   * Only ever produced by a version of the exporter that predates
   * {@link DeckSlide.points}. Kept because decks are files that live on disks
   * and get re-opened, not a server response that is always current.
   */
  body?: string;
  /** Metrics slide: `[label, value]` pairs. */
  metrics?: [string, string][];
  /** Metrics slide: the supporting-signals corroboration line. */
  footnote?: string;
  /** Cards slide: `[title, bullets]` pairs. */
  cards?: [string, string[]][];
  /** Cards slide: a lone card spans both columns rather than leaving one empty. */
  wide?: boolean;
  /** List slide. */
  items?: string[];
}

export interface DeckStyleConfig {
  /** Show the slide number in the corner. */
  slideNumbers: boolean;
  /** A custom footer line, e.g. a company name. `''` for none. */
  footer: string;
  /**
   * `''` | a palette role name | `#rrggbb`.
   *
   * Unresolved on purpose: a role has to be looked up against whichever palette
   * is showing, and the viewer can change that with the T key.
   */
  titleColor: string;
  headingColor: string;
  /** A CSS font stack — already resolved from the preset by Python. */
  fontFamily: string;
  /** Multiplier applied to every type size in the deck. */
  fontScale: number;
}

export interface DeckBoot {
  /** Project name — the title slide's heading and the deck rail. */
  project: string;
  /** "Last month (~2 sprints) · 2026-06-15 to 2026-07-13 · Sprint 11, Sprint 12". */
  period: string;
  /** Export date, for the credit line. */
  generated: string;
  /** The palette to open with. Always a key of {@link DeckBoot.palettes}. */
  theme: string;
  /** Every selectable palette: the four built-ins, then the user's own. */
  palettes: Record<string, DeckPalette>;
  slides: DeckSlide[];
  style: DeckStyleConfig;
}

export function readDeckBoot(): DeckBoot {
  return requireBoot<DeckBoot>();
}
