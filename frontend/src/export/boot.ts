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
import type { Tone } from '../design/tone';
import { requireBoot } from '../runtime/boot';
import type { EditPerson, EditRow } from './editing/state';
import type { PageChrome } from '../shared/chrome';
import type { CarriedStatuses, RetroGrids } from '../types/enums';

// Re-exported so a report component imports its whole payload vocabulary from
// one place. `Run` is a design primitive rather than a payload type, but it is
// the shape standup's prose arrives in, so it belongs in that vocabulary.
export type { Run };

/**
 * Page furniture, identical for every report.
 *
 * An alias rather than a declaration: the shape moved to `shared/chrome.ts`
 * when the boards, the gate and the deck started wearing the same masthead.
 * The name stays because a dozen report components and the wire fixtures import
 * it, and renaming them would be churn with no reader on the other side.
 */
export type ExportChrome = PageChrome;

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
  /** This row's path, for hanging a note or a field off it. Served docs only. */
  anchor?: string;
  edit?: EditMap;
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
  /** This row's path, for hanging a note or a field off it. Served docs only. */
  anchor?: string;
  edit?: EditMap;
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
  /** This row's path, for hanging a note or a field off it. Served docs only. */
  anchor?: string;
  edit?: EditMap;
}

export interface DeliveredItem {
  key: string;
  title: string;
  status: string;
  assignee?: string;
  /** This row's path, for hanging a note or a field off it. Served docs only. */
  anchor?: string;
  edit?: EditMap;
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

/**
 * One structured activity reference: what a bare `EvidenceLink` chip used to
 * flatten to a `(sha8, url)` pair. `url` is `""` when the exporter's scheme
 * allowlist rejected it — the row survives for the same reason the chip did.
 * `kind` is engine-produced, not validated; an unknown one renders muted.
 */
export interface EvidenceItem {
  kind: string;
  key: string;
  title: string;
  url: string;
  repo: string;
  status: string;
  time: string;
  /** Commits folded under their PR row; `[]` everywhere else. */
  children: EvidenceItem[];
  /** The tracker's own type word ("Story", "Sub-task", "Task"); "" when unknown. */
  type: string;
  /** Parent issue key ("PROJ-10", "#123"). A team-managed Jira Story carries its
   * epic here too — `subtask` is what licenses nesting, never this alone. */
  parent: string;
  /** The tracker's child-of-a-story flag; the ONLY licence to nest under `parent`. */
  subtask: boolean;
  /** Code/doc rows: exact ticket keys this change's own text or first-party
   * links name. Never fuzzy-matched — an empty list means "names none". */
  tickets: string[];
}

/** One labelled list inside a member card: Ticketing, Code, or Documentation. */
export interface StandupCategory {
  label: string;
  /** Bullet fragments, each already split by the shared prose splitter. */
  items: Run[][];
  links: EvidenceLink[];
  /** Structured evidence; `[]` on legacy reports, which fall back to `links`. */
  evidence: EvidenceItem[];
}

/**
 * One deterministic engineering-practice observation about a member's day —
 * "this PR carries no ticket reference", with the PR attached.
 *
 * `rule` is engine-produced, not validated (same treatment as `EvidenceItem.kind`
 * and the confidence label): an id this bundle doesn't recognise renders with
 * the muted fallback tone rather than failing a build, so the server can add a
 * rule without a coordinated release. `title` and `detail` ship as words — the
 * component supplies the colour, never the payload.
 */
export interface StandupPractice {
  rule: string;
  title: string;
  detail: Run[];
  /** The items observed, so the reader can check the claim in one click. */
  evidence: EvidenceLink[];
  /** The same rule fired in the previous standup — a pattern, not a one-off. */
  repeat?: boolean;
}

export interface StandupMember {
  name: string;
  /** They wrote this themselves, rather than it being derived from activity. */
  own?: boolean;
  /** Terse clauses, one bullet each — same fragmenting as the team summary. */
  summary: Run[][];
  progressNote?: Run[];
  /** Only the categories with real activity or evidence. */
  categories: StandupCategory[];
  /** Categories with prose but nothing to show — rendered as muted footnotes. */
  footnotes: Array<{ label: string; runs: Run[] }>;
  outlook?: Run[];
  blockers?: Run[];
  /** Absent when nothing fired, or when detection is off for this team. */
  practices?: StandupPractice[];
  selfReport?: Run[];
  /** `[tickets, code, docs]` — the order the chips and the activity bars use. */
  counts: [number, number, number];
  /** Leftover general links. Legacy reports carry no per-category ones. */
  links: EvidenceLink[];
  /** This member's path, for hanging a note or a field off them. Served docs only. */
  anchor?: string;
  edit?: EditMap;
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

/**
 * One cell of a generated table, or one value of a generated key/value row.
 *
 * A bare string is plain text. The object form carries a *reading* of the value
 * alongside it, and each field is a fact rather than a decoration:
 *
 * * `tone` is the analysis' own judgement — 80% completion is `ok`, 40% is
 *   `danger`. **This is the one place a `Tone` crosses the wire**, and it is
 *   deliberate: the thresholds differ per column *and* per direction (higher
 *   completion is better, higher spillover is worse), so deriving them here
 *   would mean a second copy of a domain rule that Python already owns. What
 *   travels is still the word, never the colour — `toneVar` maps it, here.
 * * `pct` says the number is a proportion, so it draws its own bar.
 * * `person` says the text names a human, which is what earns an avatar.
 * * `note` is subordinate text — a ticket summary beside its key.
 */
export type Cell =
  | string
  | { t: string; tone?: Tone; pct?: number; href?: string; note?: string; person?: boolean };

/**
 * A block of generated document content.
 *
 * **This is a weaker contract than the interfaces above, on purpose.** Every
 * other report here has a shape worth naming — a poker session has tickets and
 * votes, a retro has columns. The team profile does not: it is twenty-odd
 * *generated sections* whose composition depends on which analyses were enabled
 * and which sources answered, and counted across them the whole report is 14
 * key/value tables, 12 data tables, 18 cards, 14 lists and a handful of bars.
 *
 * Twenty bespoke interfaces would describe the same data worse — they would
 * assert a structure the analysis does not have, and every new sub-analysis
 * would need one more. So the exporter emits blocks, and this side draws them.
 *
 * A block still says *what kind of thing it is*, never how it looks: `kv`, not
 * "two-column grey table". The presentation rule is unchanged.
 */
export type Block =
  | { kind: 'kv'; title?: string; rows: Array<[string, Cell]> }
  | { kind: 'table'; title?: string; headers: string[]; rows: Cell[][]; numeric?: number[] }
  | { kind: 'cards'; title?: string; cards: Array<{ title: string; items: Run[][] }> }
  | { kind: 'bullets'; title?: string; ordered?: boolean; items: Run[][] }
  | { kind: 'prose'; text: string }
  /** A caveat about what the numbers can and cannot show. Rendered muted. */
  | { kind: 'note'; text: string }
  /** A finding worth stopping on — a bottleneck, a recommendation, a warning. */
  | { kind: 'callout'; tone: Tone; title: string; text?: string; items?: Run[][] }
  /** A counted breakdown, `[label, count]`. Drawn as one bar plus its key. */
  | { kind: 'bar'; label: string; counts: Array<[string, number]> }
  /** A series over time — sprint scope per day. Drawn by the shared trend card. */
  | { kind: 'trend'; trend: Trend }
  /** A chart PNG, embedded as a `data:` URI. Only where a real chart exists. */
  | { kind: 'image'; src: string; alt: string };

export interface ProfileSection {
  id: string;
  title: string;
  blocks: Block[];
}

/**
 * Something a reader added to a generated report that its schema had no room for.
 *
 * Two shapes, told apart by `kind`: a `note` is free text, a `field` is a named
 * value (`label` carries the name). Attribution is **self-declared** — the
 * author typed it into their own browser — so a renderer must never draw a
 * verified badge beside it.
 *
 * `anchor` is the edit-path of the row this hangs off, or `''` for the document
 * as a whole.
 */
export interface AnnotationRow {
  kind: string;
  anchor: string;
  label: string;
  text: string;
  author: string;
  avatar: string;
  at: string;
}

/**
 * Carried by every report, and absent whenever a document has none.
 *
 * Intersected into the union rather than repeated on nine members: narrowing on
 * `kind` still works through an intersection, so `Report.tsx` keeps its
 * exhaustiveness guard while every report gains the key for free.
 */
export interface Annotated {
  annotations?: AnnotationRow[];
}

export type ExportReport = (
  | { kind: 'anonymize'; markdown: string; warnings: string[] }
  | { kind: 'roadmap'; summary: string; projects: RoadmapProject[]; warnings: string[]; edit?: EditMap }
  | {
      kind: 'performance';
      engineer: string;
      /** The one free-prose block an artifact may open with (sprint work, overall assessment). */
      lead?: {
        title: string;
        text: string;
        /** Which artifact field this prose is, for the editor. Served docs only. */
        field?: string;
      };
      sections: PerfSection[];
      footnote?: string;
      warnings: string[];
      edit?: EditMap;
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
      edit?: EditMap;
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
      edit?: EditMap;
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
      /**
       * The report-level editable fields — `team_summary` and
       * `confidence_rationale`. Served documents only.
       *
       * Declared here because the server has always sent it. The
       * response-direction guard only catches a *dropped* field: `wire.ts`
       * asserts the committed fixture `satisfies` this union, and excess-property
       * checking does not run on imported JSON, so a field that crossed the wire
       * undeclared and was dropped on the floor by `Report.tsx` looked exactly
       * like a field nobody sends.
       */
      edit?: EditMap;
      /** The team summary, one run-list per sentence. */
      summary: Run[][];
      members: StandupMember[];
      activityCounts: Array<[string, number]>;
      activityWindow: string;
      /** `[category, status]` — how completely each source could be read. */
      coverage: Array<[string, string]>;
      /** `[source, reason]` for the sources that were not read at all. */
      skipped: Array<[string, string]>;
      /** Team rollup. `count` is MEMBERS with that signal, not signal count. */
      practices: Array<{ rule: string; count: number; title: string }>;
      /** Screenshots, embedded as `data:` URIs so the file stays portable. */
      images: string[];
      trend: Trend | null;
      warnings: string[];
      /**
       * A live share server is behind this page and will accept a verdict on a
       * practice signal. Absent for every written export — a file has nowhere
       * to send one, and the controls must not appear where they'd do nothing.
       */
      correctable?: boolean;
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
    }
  | {
      kind: 'profile';
      /** What the analysis could and could not read. Shown before the numbers. */
      coverage: string[];
      sections: ProfileSection[];
    }
) &
  Annotated;

/**
 * Present only on a tunnel-served editable document.
 *
 * **Absent for a file on disk**, and that absence is the whole switch: `main.tsx`
 * never reaches the edit stack without it, so an export written to disk runs no
 * network code. `tests/_pages.assert_inert` checks that from the other side.
 *
 * Carries no secret. `GET /` is unauthenticated for the gate, and the same
 * renderer writes documents to disk — a token here would be in both.
 */
/**
 * Where one editable region lives, and what it currently says.
 *
 * `path` addresses the **artifact**, not this payload. The two are not the same
 * shape and cannot be: `_team_summary_runs` shreds prose into sentences of link
 * runs with no inverse, so an editor opened on what is drawn could never hand
 * back something the server can store. `value` is the raw field, which is what
 * the editor opens on and what the server replaces.
 *
 * Present only on a document served editable. A file export has no `edit` keys
 * at all, which is what keeps a downloaded report byte-for-byte what it was.
 */
export interface EditTarget {
  path: string;
  value: string;
}

/** The editable fields of one payload node, keyed by artifact field name. */
export type EditMap = Record<string, EditTarget>;

export interface EditBoot {
  revision: number;
  /** False once the host has closed editing: history shows, affordances do not. */
  editable: boolean;
  edits: EditRow[];
  people: EditPerson[];
}

export interface ExportBoot {
  chrome: ExportChrome;
  report: ExportReport;
  editing?: EditBoot;
}

export function readExportBoot(): ExportBoot {
  return requireBoot<ExportBoot>();
}
