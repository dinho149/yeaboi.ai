/**
 * The wire shapes of an editable document.
 *
 * These live under `export/editing/` rather than in `types/board.ts` because
 * that file is the live boards' vocabulary, and putting a document's state there
 * would drag `ExportReport` into four bundles that never render one.
 *
 * The server sends the **whole materialised document** on every frame and the
 * client never applies an edit locally. That is more bytes than a patch stream
 * and it removes a class of bug: there is exactly one place that decides what
 * the document says, and it is the same place for this page and for the file
 * someone downloads afterwards.
 */

import type { AnnotationRow, ExportChrome, ExportReport } from '../boot';
import type { EditOps } from '../../types/enums';

export type { EditOps };

/** One recorded correction. */
export interface EditRow {
  id: string;
  /** Position in the log. Version N of the document is the first N entries. */
  seq: number;
  op: EditOps;
  /** Empty for a revert, and an *anchor* rather than a field for a note. */
  path: string;
  value: string;
  /** The name of a reader-added field; empty otherwise. */
  label: string;
  /** The edit a revert undoes; empty otherwise. */
  target: string;
  /**
   * Self-declared. Whoever held the link typed it into their own browser, so
   * nothing rendered from this may imply it was checked.
   */
  author: string;
  avatar: string;
  /** ISO-8601, UTC. */
  at: string;
  /**
   * Whether this browser made it — computed server-side by comparing pids, so a
   * raw pid never has to travel to everyone in order to answer it.
   */
  mine: boolean;
}

/**
 * Someone currently looking at the document.
 *
 * Carries no pid. A pid is the authorship key — it is what `mine` is computed
 * from — so shipping everybody's to everybody would let one reader claim
 * another's edits. `mine` is the only question this side actually asks.
 */
export interface EditPerson {
  name: string;
  avatar: string;
  /** The path they have open, or `''`. Drives "Ada is editing this". */
  editing: string;
  mine: boolean;
}

/**
 * What `GET /api/state` answers with. Satisfies `Revisioned`, so the shared
 * board store's monotonic guard drops a stale frame here exactly as it does on
 * the boards.
 */
export interface EditDocState {
  revision: number;
  /** False once the host has closed editing. */
  editable: boolean;
  chrome: ExportChrome;
  report: ExportReport;
  edits: EditRow[];
  people: EditPerson[];
}

/** Reader-added rows, re-exported so a component imports one vocabulary. */
export type { AnnotationRow };
