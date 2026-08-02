/**
 * Whether this document is being edited, and how to edit it.
 *
 * The context defaults to `null`, and every primitive short-circuits on it.
 * That is the whole trick: the nine report components and their nine existing
 * test files render with no provider and behave exactly as they did, so an
 * export written to disk is inert by construction rather than by a flag someone
 * has to remember to check.
 */

import { createContext, useContext } from 'react';

import type { EditActions } from '../actions';
import type { EditRow } from './state';

export interface Editing {
  /** False for a document the host has closed: history shows, affordances do not. */
  enabled: boolean;
  actions: EditActions;
  /** This browser's declared name and avatar, for attributing what it sends. */
  me: { name: string; avatar: string };
  /** Corrections already recorded, indexed by the path they touched. */
  byPath: ReadonlyMap<string, readonly EditRow[]>;
  /** The document revision an edit should claim to be based on. */
  revision: number;
  /** Open the history panel filtered to one path. */
  showHistory(path: string): void;
  /** Who else has this path open, for the "Ada is editing this" hint. */
  othersEditing(path: string): readonly string[];
}

const Ctx = createContext<Editing | null>(null);

export const EditProvider = Ctx.Provider;

/** `null` on a file on disk. Every primitive returns its children unchanged. */
export function useEditing(): Editing | null {
  return useContext(Ctx);
}

/** Group a log into the paths it touched, for the edited markers. */
export function indexByPath(edits: readonly EditRow[]): ReadonlyMap<string, readonly EditRow[]> {
  const out = new Map<string, EditRow[]>();
  for (const edit of edits) {
    if (!edit.path) continue;
    const rows = out.get(edit.path);
    if (rows) rows.push(edit);
    else out.set(edit.path, [edit]);
  }
  return out;
}
