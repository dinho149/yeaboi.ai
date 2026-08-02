/**
 * Every request an editable document makes.
 *
 * Shaped to be *checkable*: `tests/unit/test_web_request_keys.py` parses every
 * `mutate` call out of this file and requires each key it sends to be one the
 * Python handler actually reads. That guard only works if the paths are string
 * literals and the bodies are literal objects, so both are — a spread of a typed
 * object, or a path carrying a query string, silently escapes the parser and the
 * drift it exists to catch comes back.
 *
 * Presence is routed through here rather than through the shared
 * `hooks/useHeartbeat`, which hardcodes its wire keys inside the hook and is
 * therefore invisible to that guard today.
 */

import { postJSON, type Session } from '../runtime/api';
import type { BoardStore } from '../store/boardStore';
import type { EditDocState } from './editing/state';

export interface EditActions {
  /** Replace the text at one path. `base` is what the editor could see. */
  setText(path: string, value: string, base: string): Promise<EditOutcome>;
  /** Append an item to a list. `path` must name the append slot, `xs[-]`. */
  appendItem(path: string, value: string): Promise<EditOutcome>;
  /** Drop one item from a list. `base` guards against an index that drifted. */
  removeItem(path: string, base: string): Promise<EditOutcome>;
  /** Attach free text to a row (or to the document, with an empty `anchor`). */
  addNote(anchor: string, value: string): Promise<EditOutcome>;
  /** Attach a named value the schema never had. */
  addField(anchor: string, label: string, value: string): Promise<EditOutcome>;
  /** Undo one recorded edit. Itself recorded, and itself attributed. */
  revert(target: string): Promise<EditOutcome>;
  /** Heartbeat. Answers ok only — the long poll is what carries state. */
  presence(name: string, avatar: string, editing: string): Promise<void>;
  /** Host-only: freeze or unfreeze editing. */
  setLocked(locked: boolean): Promise<void>;
  /** Host-only: drop the most recent edit outright. */
  dropLast(): Promise<void>;
}

/**
 * What happened, in terms the UI can act on.
 *
 * `'conflict'` is separate from `'error'` because it is the one failure with a
 * sensible next step — the document moved, here is what it says now, decide
 * again — and collapsing it into a generic failure would lose that.
 */
export type EditOutcome = { ok: true } | { ok: false; kind: 'conflict' | 'error'; message: string };

interface Identity {
  name: string;
  avatar: string;
}

/** Mint a client-side id so a retried POST cannot append the same edit twice. */
function newEditId(): string {
  const random = globalThis.crypto?.randomUUID?.();
  if (random) return random;
  // `randomUUID` needs a secure context. A tunnel is https and loopback counts,
  // so this is the LAN-over-http case; uniqueness only has to hold within one
  // document, and the server rejects a duplicate id regardless.
  return `e-${Date.now().toString(36)}-${Math.floor(Math.random() * 1e9).toString(36)}`;
}

export function createEditActions(
  session: Session,
  store: BoardStore<EditDocState>,
  identity: () => Identity,
  revision: () => number
): EditActions {
  async function mutate(path: string, body: Record<string, unknown>): Promise<EditOutcome> {
    const result = await postJSON<{ ok?: boolean; error?: string; state?: EditDocState }>(session, path, body);
    // Every answer carries fresh state, including the refusals — so a conflict
    // updates the page to the newer text at the same moment it reports one.
    // The store's monotonic guard drops it if it is somehow behind.
    if (result.data?.state) store.apply(result.data.state);
    if (result.ok) return { ok: true };
    return {
      ok: false,
      kind: result.status === 409 ? 'conflict' : 'error',
      message: result.data?.error ?? describe(result.status),
    };
  }

  function describe(status: number): string {
    if (status === 409) return 'Someone else changed this first — here is what it says now.';
    if (status === 0) return 'Could not reach the document.';
    if (status === 413) return 'That is too long to save.';
    return 'That could not be saved.';
  }

  return {
    setText: (path, value, base) => {
      const who = identity();
      return mutate('/api/edit', {
        edit_id: newEditId(),
        op: 'set',
        path,
        value,
        base,
        author: who.name,
        avatar: who.avatar,
        if_revision: revision(),
      });
    },
    appendItem: (path, value) => {
      const who = identity();
      return mutate('/api/edit', {
        edit_id: newEditId(),
        op: 'append',
        path,
        value,
        author: who.name,
        avatar: who.avatar,
      });
    },
    removeItem: (path, base) => {
      const who = identity();
      return mutate('/api/edit', {
        edit_id: newEditId(),
        op: 'remove',
        path,
        base,
        author: who.name,
        avatar: who.avatar,
        if_revision: revision(),
      });
    },
    addNote: (anchor, value) => {
      const who = identity();
      return mutate('/api/edit', {
        edit_id: newEditId(),
        op: 'note',
        path: anchor,
        value,
        author: who.name,
        avatar: who.avatar,
      });
    },
    addField: (anchor, label, value) => {
      const who = identity();
      return mutate('/api/edit', {
        edit_id: newEditId(),
        op: 'field',
        path: anchor,
        label,
        value,
        author: who.name,
        avatar: who.avatar,
      });
    },
    revert: (target) => {
      const who = identity();
      return mutate('/api/edit', {
        edit_id: newEditId(),
        op: 'revert',
        target,
        author: who.name,
        avatar: who.avatar,
      });
    },
    presence: async (name, avatar, editing) => {
      await mutate('/api/presence', { name, avatar, editing });
    },
    setLocked: async (locked) => {
      await mutate('/api/admin/lock', { locked });
    },
    dropLast: async () => {
      await mutate('/api/admin/revert', {});
    },
  };
}
