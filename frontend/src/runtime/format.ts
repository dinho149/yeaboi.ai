/**
 * Value formatting shared by every surface.
 *
 * All of these render into the "numbers voice" (`--font-mono`,
 * `font-variant-numeric: tabular-nums`) so a countdown does not jitter as its
 * digits change width, and a column of counts stays aligned.
 */

/** A relative timestamp plus the absolute one for the `title` tooltip. */
export interface Ago {
  label: string;
  title: string;
}

/**
 * Format an ISO timestamp as "just now" / "42s" / "7m" / "14:05".
 *
 * `now` is injectable so tests do not depend on the wall clock — the old
 * implementation read `Date.now()` directly and was therefore untestable.
 * Returns `null` for an unparseable value rather than the string "NaN", which
 * is what `new Date(bad).toLocaleString()` would otherwise put on screen.
 */
export function fmtAgo(iso: string, now: number = Date.now()): Ago | null {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  const seconds = Math.max(0, Math.round((now - t) / 1000));
  const date = new Date(t);
  let label: string;
  if (seconds < 10) label = 'just now';
  else if (seconds < 60) label = `${seconds}s`;
  else if (seconds < 3600) label = `${Math.floor(seconds / 60)}m`;
  else label = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  return { label, title: date.toLocaleString() };
}

/** Format seconds as `MM:SS`, zero-padded, clamped at zero. Hours roll into minutes. */
export function fmtClock(totalSeconds: number): string {
  const clamped = Math.max(0, Math.round(totalSeconds));
  const minutes = Math.floor(clamped / 60);
  const seconds = clamped % 60;
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
}

/**
 * Initials for an avatar circle: first alphanumeric of the first and last words.
 *
 * "Alice Johnson" → "AJ", "alice" → "A", "🙂" → "?". Mirrors
 * `html_theme.avatar` so a member looks the same in a live board and in an
 * export of that board.
 */
export function initials(name: string): string {
  const tokens = (name || '').split(/\s+/).filter(Boolean);
  const picks = tokens.length <= 1 ? tokens.slice(0, 1) : [tokens[0], tokens[tokens.length - 1]];
  const letters: string[] = [];
  for (const token of picks) {
    const first = [...(token ?? '')].find((ch) => /[\p{L}\p{N}]/u.test(ch));
    if (first) letters.push(first.toUpperCase());
  }
  return letters.join('') || '?';
}

/**
 * A stable small integer for a name, for picking a deterministic avatar colour.
 *
 * A plain sum of code points, matching the Python side exactly. Deliberately
 * not a real hash: the requirement is that the same person gets the same colour
 * in the browser and in the exported HTML, which rules out anything the two
 * runtimes could compute differently.
 */
export function nameDigest(name: string): number {
  let total = 0;
  for (const ch of name || '') total += ch.codePointAt(0) ?? 0;
  return total;
}
