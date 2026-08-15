/**
 * The court of the poker deck: the mascot in costume, drawn as pixel art.
 *
 * A Fibonacci deck has no ranks to translate, so the court is cast by *role*.
 * The cards that mean something other than a number are the ones that get a
 * face: 13 and 21 say "too big to estimate", `?` says "I don't know", and `☕`
 * says "I need a break". Everything from 0 to 8 is an ordinary pip.
 *
 * Each sprite is a character grid over a shared silhouette. A persona supplies
 * a palette and, where it needs one, an overlay grid drawn on top — the same
 * duck in a different hat, which is how the sprite sheet already works.
 */

/** Palette slots. `.` is transparent and never painted. */
export type Ink = 'k' | 'h' | 'b' | 'e' | 'i' | 'y' | 'w' | 'f' | 'c' | 'd' | 'g';

/** 20 columns. Facing left, head over body, feet under. */
const DUCK: readonly string[] = [
  '......kkkkk.........',
  '....kkhhhhhkk.......',
  '...khhhhhhhhhk......',
  '..khhhhhhhhhhhk.....',
  '..khheeihhhhhhk.....',
  '..khheeehhhhhhk.....',
  'kkkhhhhhhhhhhhk.....',
  'kbbkhhhhhhhhhhk.....',
  'kbbbkhhhhhhhhhk.....',
  'kbbkkhhhhhhhhhk.....',
  '.kkk.khhhhhhhk......',
  '.....kyyyyyyyk......',
  '....kyyyyyyyyykk....',
  '...kyyyyyyyyyyyykk..',
  '..kyyyyyyyyyyyyyyyk.',
  '.kyyywwwwwwwyyyyyyyk',
  '.kyywwwwwwwwwyyyyyyk',
  'kyywwwwwwwwwwwyyyyyk',
  'kyyywwwwwwwwwyyyyyyk',
  '.kyyywwwwwwwyyyyyyk.',
  '..kyyyyyyyyyyyyyyk..',
  '...kkyyyyyyyyyykk...',
  '.....kkkkkkkkkk.....',
  '.....kffk..kffk.....',
  '....kffffk.kffffk...',
];

/** Overlays keep the silhouette and paint over it. `.` leaves a pixel alone. */
const CROWN: readonly string[] = [
  '....d...d...d.......',
  '....ccc.ccc.ccc.....',
  '...kccccccccccck....',
  '...kcccccccccck.....',
];

const TIARA: readonly string[] = [
  '.......d.d..........',
  '.....kcccccck.......',
  '.....kccccck........',
];

const JESTER: readonly string[] = [
  '...d.........d......',
  '...ccc.ccc.ccc......',
  '...kccccccccck......',
  '...kcccccccck.......',
];

/* Beside the head, in the clear — the body rows have no room for a prop. */
const MUG: readonly string[] = [
  '....................',
  '...............d.d..',
  '...............d.d..',
  '..............kccck.',
  '..............cgggck',
  '..............cgggkk',
  '..............cgggck',
  '..............kccck.',
  '...............kkk..',
];

export interface Persona {
  /** Card face this dresses. */
  value: string;
  /** Court name, shown on the card. */
  name: string;
  colors: Partial<Record<Ink, string>>;
  overlay?: readonly string[];
}

const BASE: Record<Ink, string> = {
  k: '#12100f',
  h: '#2f6f5e',
  b: '#e8952f',
  e: '#12100f',
  i: '#f4f6f4',
  y: '#cdd3cd',
  w: '#8fa3a0',
  f: '#e8952f',
  c: '#d8b44a',
  d: '#e8d98a',
  g: '#6b4a33',
};

/** The four cards that say something other than a number. */
export const COURT: readonly Persona[] = [
  {
    value: '21',
    name: 'King',
    colors: { h: '#4a3f7a', y: '#e6e2f2', w: '#a99fd0', c: '#d8b44a', d: '#f2e6a8' },
    overlay: CROWN,
  },
  {
    value: '13',
    name: 'Queen',
    colors: { h: '#7a3f5e', y: '#f2e2ea', w: '#d0a0b8', c: '#d8b44a', d: '#f2e6a8' },
    overlay: TIARA,
  },
  {
    value: '?',
    name: 'Jester',
    colors: { h: '#8a3f2f', y: '#f0dfc8', w: '#c99a6a', c: '#c4543f', d: '#e8c84a' },
    overlay: JESTER,
  },
  {
    value: '☕',
    name: 'Barista',
    colors: { h: '#3f5a7a', y: '#e4e8ee', w: '#9aaec4', c: '#d8ded8', g: '#6b4a33' },
    overlay: MUG,
  },
];

export const COURT_BY_VALUE: ReadonlyMap<string, Persona> = new Map(COURT.map((p) => [p.value, p]));

export const SPRITE_W = 20;
export const SPRITE_H = DUCK.length;

/**
 * One SVG path per colour: every pixel of that colour as a closed unit square.
 * Grouping this way keeps a card to a handful of nodes instead of ~400 rects.
 */
export function spritePaths(persona: Persona): { fill: string; d: string }[] {
  const palette = { ...BASE, ...persona.colors };
  const grid = DUCK.map((row) => row.split(''));

  persona.overlay?.forEach((row, y) => {
    row.split('').forEach((ch, x) => {
      if (ch !== '.' && grid[y]?.[x] !== undefined) grid[y][x] = ch;
    });
  });

  const byColor = new Map<string, string[]>();
  grid.forEach((row, y) => {
    row.forEach((ch, x) => {
      if (ch === '.') return;
      const fill = palette[ch as Ink];
      if (!fill) return;
      const runs = byColor.get(fill) ?? [];
      runs.push(`M${x} ${y}h1v1h-1z`);
      byColor.set(fill, runs);
    });
  });

  return [...byColor].map(([fill, runs]) => ({ fill, d: runs.join('') }));
}
