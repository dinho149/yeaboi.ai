/**
 * A colour per retro column.
 *
 * The four columns were visually identical: same border, same surface, same
 * heading weight. Their meaning was carried by the words alone, so at a glance
 * — which is how a facilitator reads a board while someone is talking — the
 * board was an undifferentiated grid.
 *
 * Two decisions worth stating, because both are easy to get subtly wrong:
 *
 * **"Didn't go well" is `warn`, not `danger`.** Red frames a retrospective as
 * an incident review. The column is for things people found frustrating, and
 * amber is candid without being accusatory — the difference between "this was
 * hard" and "this was a failure". It is also the column people are most
 * reluctant to write in, and the colour should not add to that.
 *
 * **"Action items" gets the mode accent.** Not because it is the most urgent
 * column, but because it is the one whose contents outlive the meeting. Giving
 * it the board's own colour says that without a label.
 *
 * Typed as a total `Record`, so adding a grid to `RETRO_GRIDS` in `board.py`
 * fails the TypeScript build here rather than rendering an uncoloured column.
 */

import type { Tone } from '../design/tone';
import type { RetroGrids } from '../types/enums';

export const GRID_TONE: Record<RetroGrids, Tone> = {
  went_well: 'ok',
  didnt_go_well: 'warn',
  action_items: 'accent',
  demos: 'accent2',
};
