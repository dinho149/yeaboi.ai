/**
 * The payload contract for every static exported report.
 *
 * A report is `{ chrome, report }`: the furniture that every export shares, and
 * a discriminated union for the one that is actually being rendered. Adding a
 * report means adding a member to {@link ExportReport} and a case to the switch
 * in `Report.tsx` — which is a *compile* error until both exist, so a mode
 * cannot ship a payload nothing knows how to draw.
 *
 * Two constraints shape these shapes:
 *
 * * **No markup crosses the wire.** Everything here is text, numbers, and
 *   structure. This is the whole point of the migration — the Python exporters
 *   used to hand-assemble HTML strings and carry the escaping discipline that
 *   goes with it.
 * * **No presentation crosses it either.** No colours, no class names, no
 *   widths. The payload says a project is `large`; the tone that renders it is
 *   this side's business, so a theme change is one file.
 */

import { requireBoot } from '../runtime/boot';
import type { CarriedStatuses, RetroGrids } from '../types/enums';

/** Page furniture, identical for every report. */
export interface ExportChrome {
  /**
   * Mode key, set as `[data-mode]` on `<html>` by the server and driving
   * `--accent`. Not every export owns a distinct TUI accent — roadmap borrows
   * planning's, anonymize the default — so this is the *accent* to wear, not a
   * claim about which mode wrote the file.
   */
  mode: string;
  /** Terminal title-bar text, conventionally `yeaboi — <mode>`. */
  frame: string;
  /** Word set in the block-glyph face. Kept short: the face is two rows and wide. */
  wordmark: string;
  title: string;
  subtitle?: string;
  /** Header eyebrows, `[label, value]`. Each must say something true about the run. */
  facts?: Array<[string, string]>;
  badges?: string[];
  /** Sticky contents links, `[sectionId, label]`. Omit for a single-screen report. */
  nav?: Array<[string, string]>;
  footer: string;
}

/**
 * A run-over-run series, drawn as the trend card at the top of a report.
 *
 * Built by `html_theme.trend`, which normalises a mode's store history: newest
 * first in, oldest first out, same-day re-runs deduped, and anything dated after
 * the report itself dropped — re-exporting June's retro must not draw July's.
 *
 * `null` rather than an absent key, because "fewer than two runs" is a state the
 * server has decided about; an omitted field would look like a payload bug.
 */
export interface Trend {
  /** Card heading, e.g. `Card volume trend`. */
  title: string;
  /** Accessible chart description. A chart with no label is invisible to AT. */
  label: string;
  /** `[date, value]`, oldest first. */
  points: Array<[string, number]>;
}

export interface RoadmapProject {
  index: number;
  name: string;
  /** `large` | `small`. Anything else is treated as small, matching the engine. */
  size: string;
  quarter?: string;
  themes?: string[];
  description?: string;
  rationale?: string;
}

/** A titled run of bullets — the shape all three performance artifacts share. */
export interface PerfSection {
  title: string;
  items: string[];
}

/** One accepted vote. `value` is a `POKER_DECK` card, so `?` and `☕` are legal. */
export interface PokerVote {
  voter: string;
  value: string;
}

export interface PokerTicket {
  key: string;
  /** Tracker link. Routed through `safeUrl`, so an unsafe scheme degrades to text. */
  url?: string;
  summary: string;
  /** Points already on the tracker before the room voted. */
  before: number | null;
  /** Points the room agreed. `null` whenever `estimated` is false. */
  final: number | null;
  /** False when the room skipped the ticket — which is an outcome, not a gap. */
  estimated: boolean;
  votes: PokerVote[];
  aiNote?: string;
  duel?: { low: string; high: string; transcript: string };
}

export interface RetroCard {
  text: string;
  author?: string;
  /** Written by the AI facilitator. Attributed as such rather than to a person. */
  ai?: boolean;
  /** `[emoji, count]`, non-zero counts only. */
  reactions: Array<[string, number]>;
}

/**
 * One retro column.
 *
 * Carries the grid *key*, never its heading or its colour: `RETRO_GRID_LABELS`
 * is codegen'd from `retro/board.py` into `types/enums.ts`, so shipping the
 * label too would let a stale bundle disagree with the server about what the
 * column is called. Empty columns are sent as well — whether an empty column
 * gets a card or a footnote is a layout decision, and layout lives here.
 */
export interface RetroColumn {
  grid: RetroGrids;
  cards: RetroCard[];
}

export interface CarriedItem {
  status: CarriedStatuses;
  text: string;
}

export interface DeliveredItem {
  key: string;
  title: string;
  status: string;
  assignee?: string;
}

export interface ReportTheme {
  title: string;
  outcomes: string[];
}

export type ExportReport =
  | { kind: 'anonymize'; markdown: string; warnings: string[] }
  | { kind: 'roadmap'; summary: string; projects: RoadmapProject[]; warnings: string[] }
  | {
      kind: 'performance';
      engineer: string;
      /** The one free-prose block an artifact may open with (sprint work, overall assessment). */
      lead?: { title: string; text: string };
      sections: PerfSection[];
      footnote?: string;
      warnings: string[];
    }
  | {
      kind: 'poker';
      tickets: PokerTicket[];
      participants: string[];
      trend: Trend | null;
    }
  | {
      kind: 'retro';
      /** All four, in board order, including the empty ones. */
      columns: RetroColumn[];
      participants: string[];
      /** Last sprint's action items and the progress recorded against them. */
      carried: CarriedItem[];
      trend: Trend | null;
    }
  | {
      kind: 'reporting';
      /**
       * The model's one-line reading of the period. Empty rather than absent —
       * unlike the optional fields above, which the exporter omits: these two
       * are on every `DeliveryReport` and `""` is what "the model said nothing"
       * looks like there, so the payload says the same thing the artifact does.
       */
      headline: string;
      /** `[label, value]`. Values arrive already formatted — "12 days", "94%". */
      metrics: Array<[string, string]>;
      summary: string;
      themes: ReportTheme[];
      highlights: string[];
      items: DeliveredItem[];
      /**
       * `[label, count]` for the delivered-work breakdown — by person where
       * there is more than one, else by status. Which of the two it is is a
       * server decision, so only the resulting pairs travel.
       */
      breakdown: Array<[string, number]>;
      /**
       * The decoration the host picked per section slot, e.g. `{ metrics: '📊' }`.
       * The *vocabulary* is server-validated and codegen'd; this is the choice.
       */
      emoji: Record<string, string>;
      trend: Trend | null;
      warnings: string[];
    };

export interface ExportBoot {
  chrome: ExportChrome;
  report: ExportReport;
}

export function readExportBoot(): ExportBoot {
  return requireBoot<ExportBoot>();
}
