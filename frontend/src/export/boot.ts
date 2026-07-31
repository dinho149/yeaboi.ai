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

import type { Run } from '../design/primitives';
import { requireBoot } from '../runtime/boot';
import type { CarriedStatuses, RetroGrids } from '../types/enums';

// Re-exported so a report component imports its whole payload vocabulary from
// one place. `Run` is a design primitive rather than a payload type, but it is
// the shape standup's prose arrives in, so it belongs in that vocabulary.
export type { Run };

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
  /**
   * Bounds on the drawn domain. Facts about the series, not about the drawing:
   * a confidence percentage cannot exceed 100, so padding past it would claim
   * headroom that does not exist. Absent means unbounded that way.
   */
  floor?: number;
  ceiling?: number;
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

/**
 * An evidence link, `[label, url]`.
 *
 * The URL is `""` when the exporter's scheme allowlist rejected it, and the row
 * survives anyway: the label says what the link was *evidence of*, and dropping
 * it would silently shrink the evidence a reader is being shown.
 */
export type EvidenceLink = [string, string];

/** One labelled list inside a member card: Ticketing, Code, or Documentation. */
export interface StandupCategory {
  label: string;
  /** Bullet fragments, each already split by the shared prose splitter. */
  items: Run[][];
  links: EvidenceLink[];
}

export interface StandupMember {
  name: string;
  /** They wrote this themselves, rather than it being derived from activity. */
  own?: boolean;
  summary: Run[];
  progressNote?: Run[];
  /** Only the categories with real activity or evidence. */
  categories: StandupCategory[];
  /** Categories with prose but nothing to show — rendered as muted footnotes. */
  footnotes: Array<{ label: string; runs: Run[] }>;
  outlook?: Run[];
  blockers?: Run[];
  selfReport?: Run[];
  /** `[tickets, code, docs]` — the order the chips and the activity bars use. */
  counts: [number, number, number];
  /** Leftover general links. Legacy reports carry no per-category ones. */
  links: EvidenceLink[];
}

export interface PlanFeature {
  id: string;
  title: string;
  description: string;
  /** `critical` | `high` | `medium` | `low`. Unknown values render neutral. */
  priority: string;
}

export interface PlanStory {
  id: string;
  title: string;
  /** The "As a X, I want Y, so that Z" sentence. */
  text: string;
  priority: string;
  discipline: string;
  points: number;
  rationale?: string;
  /** `high` | `medium` | `low` — how sure the estimate is. */
  confidence?: string;
  acceptanceCriteria: Array<{ given: string; when: string; then: string }>;
  /**
   * `[item, applicable]` pairs, already zipped.
   *
   * Sent paired rather than as two lists because the old renderer zipped them
   * itself behind a length check, and a mismatch silently dropped the whole
   * block. Empty when the story's flags did not line up with the team's DoD.
   */
  dod: Array<[string, boolean]>;
}

export interface PlanTask {
  id: string;
  title: string;
  description: string;
  label: string;
  testPlan?: string;
  aiPrompt?: string;
}

export interface PlanSprint {
  name: string;
  goal: string;
  /** Points this sprint can hold, after capacity deductions. */
  capacity: number;
  /** Points the planned stories actually add up to. */
  used: number;
  storyIds: string[];
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
    }
  | {
      kind: 'standup';
      sprint: { name: string; day: number; total: number };
      /**
       * `label` and `trend` are produced by the engine, not validated against
       * untrusted input, so they travel as their own strings and this side maps
       * them to tones with a fallback. An unfamiliar label goes muted rather
       * than failing a build.
       */
      confidence: {
        label: string;
        pct: number;
        text: string;
        trend: string;
        trendText: string;
        rationale: string;
      };
      /** The team summary, one run-list per sentence. */
      summary: Run[][];
      members: StandupMember[];
      activityCounts: Array<[string, number]>;
      activityWindow: string;
      /** `[category, status]` — how completely each source could be read. */
      coverage: Array<[string, string]>;
      /** `[source, reason]` for the sources that were not read at all. */
      skipped: Array<[string, string]>;
      /** Screenshots, embedded as `data:` URIs so the file stays portable. */
      images: string[];
      trend: Trend | null;
      warnings: string[];
    }
  | {
      kind: 'plan';
      /** `[label, question, answer]`. Empty before intake has been answered. */
      questionnaire: Array<[string, string, string]>;
      /**
       * `null` until the analyzer has run. Every section below is likewise
       * empty rather than absent at its own checkpoint — a plan exported
       * mid-pipeline is a normal artifact, not a broken one.
       */
      analysis: {
        name: string;
        description: string;
        targetState: string;
        projectType: string;
        sprintWeeks: number;
        targetSprints: number;
        fields: Array<{ label: string; items: string[] }>;
      } | null;
      capacity: {
        teamSize: number;
        sprintWeeks: number;
        targetSprints: number;
        velocity: number;
        netVelocity: number;
        /** Pre-formatted phrases — "bank holidays: 2d", "discovery: 5%". */
        deductions: string[];
      } | null;
      /** The tracker key the epic was pushed to, when it has been. */
      epicKey: string;
      features: PlanFeature[];
      storyGroups: Array<{ featureId: string; featureTitle: string; stories: PlanStory[] }>;
      /** `[discipline, points]`, sorted by discipline. */
      pointsByDiscipline: Array<[string, number]>;
      taskGroups: Array<{ storyId: string; storyText: string; tasks: PlanTask[] }>;
      sprints: PlanSprint[];
      /** Gross velocity, for comparing against a sprint's reduced capacity. */
      velocity: number;
      images: string[];
    };

export interface ExportBoot {
  chrome: ExportChrome;
  report: ExportReport;
}

export function readExportBoot(): ExportBoot {
  return requireBoot<ExportBoot>();
}
