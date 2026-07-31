/**
 * The random display-name generator behind the 🎲 button.
 *
 * The word lists are **not** hardcoded here. They live in `retro/board.py` and
 * arrive in the boot payload, so the browser and the TUI offer the same names
 * and adding a word is a one-file change. (Poker importing retro's private
 * `_ADJECTIVES` is the last cross-mode page coupling; routing both through the
 * payload is what lets phase 7 delete it.)
 */

/** Pick one "Adjective Noun" pair. `pick` is injectable so tests are deterministic. */
export function randomName(
  adjectives: readonly string[],
  nouns: readonly string[],
  pick: (n: number) => number = (n) => Math.floor(Math.random() * n)
): string {
  if (!adjectives.length || !nouns.length) return 'Guest';
  const adjective = adjectives[pick(adjectives.length) % adjectives.length];
  const noun = nouns[pick(nouns.length) % nouns.length];
  return `${adjective} ${noun}`;
}
