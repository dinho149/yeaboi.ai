/**
 * The court.
 *
 * A Fibonacci deck has no ranks to translate, so the cards that mean something
 * other than a count are named instead: 13 and 21 say "too big to estimate",
 * `?` says "I don't know", `☕` says "I need a break".
 */

const COURT: Readonly<Record<string, string>> = {
  '13': 'Queen',
  '21': 'King',
  '?': 'Jester',
  '☕': 'Barista',
};

export function courtName(value: string): string | undefined {
  return COURT[value];
}
