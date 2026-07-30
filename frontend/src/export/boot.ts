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
    };

export interface ExportBoot {
  chrome: ExportChrome;
  report: ExportReport;
}

export function readExportBoot(): ExportBoot {
  return requireBoot<ExportBoot>();
}
