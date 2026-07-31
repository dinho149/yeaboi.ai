/**
 * Throw-safe browser storage.
 *
 * Every direct `localStorage` call in the old board scripts was an unguarded
 * one, and `localStorage` **throws on access** — not on write, on the property
 * read itself — in Safari private mode and in any page opened over `file://`
 * with third-party storage blocked. Exports are opened that way routinely, so
 * one unguarded read takes the whole page down before it paints.
 *
 * The local/session split is a deliberate model, not an accident:
 *
 * * `localStorage` holds **who you are** — participant id, display name, avatar,
 *   palette. It survives a reload and is shared across tabs, which is right:
 *   you are the same person in both.
 * * `sessionStorage` holds **what you may see** — the board token. Per-tab, so
 *   opening a second board in another tab cannot inherit the first one's access,
 *   and closing the tab ends it. This is also why cookies are rejected outright:
 *   a cookie is shared across tabs and would silently break that model.
 */

type Store = 'local' | 'session';

function backing(kind: Store): Storage | null {
  try {
    return kind === 'local' ? window.localStorage : window.sessionStorage;
  } catch {
    return null;
  }
}

/** Read a key, or `null` when absent or storage is unavailable. */
export function read(kind: Store, key: string): string | null {
  try {
    return backing(kind)?.getItem(key) ?? null;
  } catch {
    return null;
  }
}

/** Write a key. Silently a no-op when storage is unavailable or full. */
export function write(kind: Store, key: string, value: string): void {
  try {
    backing(kind)?.setItem(key, value);
  } catch {
    /* private mode, quota, or file:// — the value still applies to this page view */
  }
}

/** Remove a key. Silently a no-op when storage is unavailable. */
export function remove(kind: Store, key: string): void {
  try {
    backing(kind)?.removeItem(key);
  } catch {
    /* nothing to do — the key was unreachable anyway */
  }
}

/**
 * Read a key, falling back to `fallback` when absent **or not one of `allowed`**.
 *
 * The allowlist matters: these values come back from a store the user can edit
 * in devtools and then get sent to the server, which validates them again. This
 * keeps the *client* from rendering a broken UI off a hand-edited avatar or a
 * palette name that no longer exists after an upgrade.
 */
export function readEnum<T extends string>(
  kind: Store,
  key: string,
  allowed: readonly T[],
  fallback: T
): T {
  const raw = read(kind, key);
  return (allowed as readonly string[]).includes(raw ?? '') ? (raw as T) : fallback;
}

/**
 * The stable per-browser participant id, minted once and reused.
 *
 * The server keys presence, card ownership and votes on this, so it is the
 * difference between "your card" and "someone's card" across a reload. Not a
 * secret and never treated as one — every action it appears in is re-checked
 * against the token server-side.
 */
export function participantId(key = 'yeaboi_pid'): string {
  const existing = read('local', key);
  if (existing) return existing;
  // randomUUID needs a secure context. A LAN board on plain http:// is not one,
  // so the fallback is load-bearing rather than defensive — it is the path most
  // teammates actually take.
  const minted =
    typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
      ? crypto.randomUUID()
      : `p${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`;
  write('local', key, minted);
  return minted;
}
